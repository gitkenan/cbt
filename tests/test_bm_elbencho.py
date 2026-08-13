"""
Validates that Elbencho correctly initializes with defaults and overrides,
merges global and per-workload configuration, integrates with benchmarkfactory,
and owns Cartesian expansion of list-valued workload parameters internally,
bypassing benchmarkfactory.expand_configs().

These tests use mock infrastructure and do not require a live cluster.
"""

import tempfile
import unittest

import benchmarkfactory
import settings
from benchmark.elbencho import Elbencho
from cluster.ceph import Ceph

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

INVARIANT_YAML = "tools/invariant.yaml"

_MINIMAL_CONFIG = {
    "iteration": 0,
    "benchmark": "elbencho",
}

_FULL_CONFIG = {
    "iteration": 0,
    "benchmark": "elbencho",
    "cmd_path": "/opt/elbencho/bin/elbencho",
    "auth": {
        "config": "access_key=AKID;secret_key=<redacted>;url=http://rgw:7480;retry=9"
    },
    "workloads": {
        "write_small": {
            "s3_bucket": "cbt-benchmark",
            "mkdirs": True,
            "threads": [1, 4, 16],
            "iodepth": [1, 4, 16],
            "blocksize": ["4k", "128k"],
            "size": "4g",
            "num_objects": 1000,
            "mode": "write",
            "direct": True,
            "duration": 60,
        },
        "read_small": {
            "s3_bucket": "cbt-benchmark",
            "threads": [1, 4, 16],
            "iodepth": [1, 4],
            "blocksize": ["4k"],
            "size": "4g",
            "num_objects": 1000,
            "mode": "read",
            "duration": 60,
        },
    },
}


class TestElbenchoDefaults(unittest.TestCase):
    """Test that construction with minimal config produces correct defaults."""

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self, config=None) -> Elbencho:
        cfg = dict(_MINIMAL_CONFIG, **(config or {}))
        b = benchmarkfactory.get_object(self.archive_dir, self.cluster, "elbencho", cfg)
        assert isinstance(b, Elbencho)
        return b

    def test_returns_elbencho_instance(self):
        b = self._make()
        self.assertIsInstance(b, Elbencho)

    def test_default_cmd_path(self):
        b = self._make()
        self.assertEqual("/usr/local/bin/elbencho", b.cmd_path)

    def test_default_auth_is_empty_dict(self):
        b = self._make()
        self.assertEqual({}, b.auth)

    def test_default_workloads_is_empty_dict(self):
        b = self._make()
        self.assertEqual({}, b.workloads)

    def test_global_defaults_cmd_path(self):
        b = self._make()
        self.assertEqual("/usr/local/bin/elbencho", b._global_defaults["cmd_path"])

    def test_global_defaults_auth(self):
        b = self._make()
        self.assertEqual({}, b._global_defaults["auth"])

    def test_global_defaults_keys_only_cmd_path_and_auth(self):
        """_global_defaults must contain exactly the two session-wide keys."""
        b = self._make()
        self.assertEqual({"cmd_path", "auth"}, set(b._global_defaults.keys()))

    def test_base_run_dir_set(self):
        b = self._make()
        self.assertIsNotNone(b.base_run_dir)
        self.assertIsInstance(b.base_run_dir, str)

    def test_archive_dir_set(self):
        b = self._make()
        self.assertIsNotNone(b.archive_dir)


class TestElbenchoExplicitConfig(unittest.TestCase):
    """Test construction with fully-specified top-level and workload config."""

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self) -> Elbencho:
        b = benchmarkfactory.get_object(
            self.archive_dir, self.cluster, "elbencho", dict(_FULL_CONFIG)
        )
        assert isinstance(b, Elbencho)
        return b

    def test_custom_cmd_path(self):
        b = self._make()
        self.assertEqual("/opt/elbencho/bin/elbencho", b.cmd_path)

    def test_custom_auth(self):
        b = self._make()
        self.assertIn("config", b.auth)
        self.assertIn("access_key=AKID", b.auth["config"])

    def test_workloads_stored_as_dict(self):
        b = self._make()
        self.assertIsInstance(b.workloads, dict)
        
    def test_workload_names_preserved(self):
        b = self._make()
        self.assertIn("write_small", b.workloads)
        self.assertIn("read_small", b.workloads)

    def test_workload_list_params_not_permuted(self):
        """List-valued workload params (threads, iodepth, blocksize) must be
        stored as lists — not expanded by the factory."""
        b = self._make()
        ws = b.workloads["write_small"]
        self.assertIsInstance(ws["threads"], list)
        self.assertIsInstance(ws["iodepth"], list)
        self.assertIsInstance(ws["blocksize"], list)
        self.assertEqual([1, 4, 16], ws["threads"])
        self.assertEqual([1, 4, 16], ws["iodepth"])
        self.assertEqual(["4k", "128k"], ws["blocksize"])

    def test_global_defaults_reflect_explicit_cmd_path(self):
        b = self._make()
        self.assertEqual("/opt/elbencho/bin/elbencho", b._global_defaults["cmd_path"])


class TestElbenchoWorkloadMerge(unittest.TestCase):
    """Test the per-workload default-merging logic in _run_workloads.

    We test the merge contract directly rather than going through run() which
    would try to contact a real cluster.
    """

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self, workloads) -> Elbencho:
        cfg = dict(_MINIMAL_CONFIG, workloads=workloads)
        b = benchmarkfactory.get_object(self.archive_dir, self.cluster, "elbencho", cfg)
        assert isinstance(b, Elbencho)
        return b

    def test_workload_mode_missing_raises_error(self):
        with self.assertRaises(ValueError) as ctx:
            self._make(
                {
                    "no_mode": {"s3_bucket": "test-bucket"},
                }
            )
        self.assertIn("missing required key 'mode'", str(ctx.exception))


class TestElbenchoExists(unittest.TestCase):
    """Test the exists() skip-guard."""

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self) -> Elbencho:
        b = benchmarkfactory.get_object(
            self.archive_dir, self.cluster, "elbencho", dict(_MINIMAL_CONFIG)
        )
        assert isinstance(b, Elbencho)
        return b

    def test_exists_false_when_archive_dir_absent(self):
        b = self._make()
        # Point archive_dir at a path that definitely does not exist.
        b.archive_dir = "/tmp/__cbt_elbencho_no_such_dir_xyzzy__"
        self.assertFalse(b.exists())

    def test_exists_true_when_archive_dir_present(self):
        b = self._make()
        with tempfile.TemporaryDirectory() as tmpdir:
            b.archive_dir = tmpdir
            self.assertTrue(b.exists())


class TestBenchmarkFactoryElbenchoIntegration(unittest.TestCase):
    """Test that benchmarkfactory wires elbencho correctly.

    Key guarantee from Story 2: the factory must NOT apply expand_configs()
    Cartesian permutation to elbencho configs.
    """

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def test_get_object_returns_elbencho(self):
        b = benchmarkfactory.get_object(
            self.archive_dir, self.cluster, "elbencho", dict(_MINIMAL_CONFIG)
        )
        self.assertIsInstance(b, Elbencho)

    def test_unknown_benchmark_returns_none(self):
        b = benchmarkfactory.get_object(
            self.archive_dir, self.cluster, "no_such_benchmark", dict(_MINIMAL_CONFIG)
        )
        self.assertIsNone(b)

    def test_get_all_yields_single_instance_despite_list_valued_workload_params(self):
        """Even though workload params contain lists (threads, iodepth, blocksize),
        get_all() must yield exactly ONE Elbencho object — not a Cartesian product."""
        settings.benchmarks = {
            "elbencho": {
                "cmd_path": "/usr/local/bin/elbencho",
                "auth": {},
                "workloads": {
                    "w": {
                        "s3_bucket": "b",
                        "threads": [1, 4, 16],
                        "iodepth": [1, 4, 16],
                        "blocksize": ["4k", "128k"],
                        "mode": "write",
                    }
                },
            }
        }
        objects = list(benchmarkfactory.get_all(self.archive_dir, self.cluster, 0))
        elbencho_objects = [o for o in objects if isinstance(o, Elbencho)]
        self.assertEqual(1, len(elbencho_objects))

    def test_elbencho_uses_workload_configs_delegation(self):
        from benchmark.elbencho import Elbencho
        self.assertTrue(hasattr(Elbencho, 'workload_configs'))
        self.assertTrue(callable(Elbencho.workload_configs))


if __name__ == "__main__":
    unittest.main()
