"""Unit tests for the Elbencho benchmark class.

Validates that Elbencho correctly initializes with defaults and overrides,
merges global and per-workload configuration, integrates with benchmarkfactory,
avoids unintended Cartesian expansion of list-valued workload parameters, and
builds correct elbencho CLI commands for each run cell.
These tests use mock infrastructure and do not require a live cluster.
"""

import tempfile
import unittest
from unittest.mock import call, patch

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

    Key guarantee from Story 2: the factory must NOT apply all_configs()
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

    def test_elbencho_uses_generate_configs_delegation(self):
        from benchmark.elbencho import Elbencho
        self.assertTrue(hasattr(Elbencho, 'generate_configs'))
        self.assertTrue(callable(Elbencho.generate_configs))


# ---------------------------------------------------------------------------
# Story 3 — command builder and run-loop tests
# ---------------------------------------------------------------------------

class TestParseBlocskizeToBytes(unittest.TestCase):
    """_parse_blocksize_to_bytes converts human-readable strings correctly."""

    def test_4k(self):
        self.assertEqual(4096, Elbencho._parse_blocksize_to_bytes("4k"))

    def test_128k(self):
        self.assertEqual(131072, Elbencho._parse_blocksize_to_bytes("128k"))

    def test_1m(self):
        self.assertEqual(1048576, Elbencho._parse_blocksize_to_bytes("1m"))

    def test_1g(self):
        self.assertEqual(1073741824, Elbencho._parse_blocksize_to_bytes("1g"))

    def test_plain_int_string(self):
        self.assertEqual(512, Elbencho._parse_blocksize_to_bytes("512"))

    def test_uppercase(self):
        self.assertEqual(4096, Elbencho._parse_blocksize_to_bytes("4K"))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            Elbencho._parse_blocksize_to_bytes("abc")


class TestBuildAuthFlags(unittest.TestCase):
    """_build_auth_flags emits the right CLI flags for each credential mode."""

    def test_config_string_all_fields(self):
        auth = {"config": "access_key=AK;secret_key=SK;url=http://rgw:7480;retry=9"}
        flags = Elbencho._build_auth_flags(auth)
        self.assertIn("--s3endpoints", flags)
        self.assertIn("http://rgw:7480", flags)
        self.assertIn("--s3key", flags)
        self.assertIn("AK", flags)
        self.assertIn("--s3secret", flags)
        self.assertIn("SK", flags)

    def test_config_string_no_url(self):
        auth = {"config": "access_key=AK;secret_key=SK"}
        flags = Elbencho._build_auth_flags(auth)
        self.assertNotIn("--s3endpoints", flags)
        self.assertIn("--s3key", flags)
        self.assertIn("--s3secret", flags)

    def test_session_token(self):
        auth = {"s3_session_token": "mytoken"}
        flags = Elbencho._build_auth_flags(auth)
        self.assertIn("--s3authtoken", flags)
        self.assertIn("mytoken", flags)

    def test_empty_auth_emits_nothing(self):
        self.assertEqual([], Elbencho._build_auth_flags({}))

    def test_env_var_mode_emits_nothing(self):
        # Option B: credentials supplied via AWS_* env vars — auth dict is empty.
        self.assertEqual([], Elbencho._build_auth_flags({}))


class TestBuildElbenchoCmd(unittest.TestCase):
    """_build_elbencho_cmd produces a valid shell command string."""

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self, workload_params: dict) -> Elbencho:
        cfg = dict(
            _MINIMAL_CONFIG,
            cmd_path="/usr/bin/elbencho",
            auth={"config": "access_key=AK;secret_key=SK;url=http://rgw:7480"},
            workloads={"w": workload_params},
        )
        b = benchmarkfactory.get_object(self.archive_dir, self.cluster, "elbencho", cfg)
        assert isinstance(b, Elbencho)
        return b

    def test_write_command_contains_mode_flag(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--write", cmd)
        self.assertNotIn("--read", cmd)

    def test_read_command_contains_mode_flag(self):
        b = self._make({"s3_bucket": "bkt", "mode": "read"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--read", cmd)
        self.assertNotIn("--write", cmd)

    def test_readwrite_contains_both_flags(self):
        b = self._make({"s3_bucket": "bkt", "mode": "readwrite"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--write", cmd)
        self.assertIn("--read", cmd)

    def test_threads_and_iodepth_in_cmd(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "128k", 16, 4, "/tmp/run")
        self.assertIn("--threads 16", cmd)
        self.assertIn("--iodepth 4", cmd)
        self.assertIn("--block 128k", cmd)

    def test_s3region_default_always_present(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--s3region default", cmd)

    def test_s3region_override(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write", "s3_region": "us-east-1"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--s3region us-east-1", cmd)

    def test_mkdirs_flag_when_set(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write", "mkdirs": True})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--mkdirs", cmd)

    def test_mkdirs_absent_when_not_set(self):
        b = self._make({"s3_bucket": "bkt", "mode": "read"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertNotIn("--mkdirs", cmd)

    def test_bucket_path_is_plain_s3_uri(self):
        b = self._make({"s3_bucket": "cbt-benchmark", "mode": "write"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("s3://cbt-benchmark", cmd)
        self.assertNotIn("$(hostname -s)", cmd)

    def test_duration_emitted_when_set(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write", "duration": 60})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--timelimit 60", cmd)

    def test_size_emitted_when_set(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write", "size": "4g"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--size 4g", cmd)

    def test_num_objects_maps_to_files_flag(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write", "num_objects": 1000})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertIn("--files 1000", cmd)

    def test_resfile_in_run_dir(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/myrun")
        self.assertIn("--resfile /tmp/myrun/result.csv", cmd)

    def test_stat_mode_returns_empty_string(self):
        b = self._make({"s3_bucket": "bkt", "mode": "stat"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertEqual("", cmd)

    def test_list_mode_returns_empty_string(self):
        b = self._make({"s3_bucket": "bkt", "mode": "list"})
        cmd = b._build_elbencho_cmd(b.workloads["w"], "4k", 1, 1, "/tmp/run")
        self.assertEqual("", cmd)

    def test_unknown_mode_raises(self):
        b = self._make({"s3_bucket": "bkt", "mode": "write"})
        bad_workload = {"s3_bucket": "bkt", "mode": "invalid"}
        with self.assertRaises(ValueError):
            b._build_elbencho_cmd(bad_workload, "4k", 1, 1, "/tmp/run")


class TestRunLoop(unittest.TestCase):
    """_run_workloads fans out the correct number of pdsh calls."""

    archive_dir = "/tmp"

    @classmethod
    def setUpClass(cls):
        settings.mock_initialize(config_file=INVARIANT_YAML)
        cls.cluster = Ceph.mockinit(settings.cluster)

    def _make(self, workloads: dict) -> Elbencho:
        cfg = dict(
            _MINIMAL_CONFIG,
            cmd_path="/usr/bin/elbencho",
            auth={},
            workloads=workloads,
        )
        b = benchmarkfactory.get_object(self.archive_dir, self.cluster, "elbencho", cfg)
        assert isinstance(b, Elbencho)
        return b

    @patch("common.make_remote_dir")
    @patch("common.pdsh")
    def test_pdsh_call_count_matches_run_matrix(self, mock_pdsh, mock_mkdir):
        """2 blocksizes × 2 threads × 2 iodepths = 8 pdsh calls."""
        mock_pdsh.return_value.communicate.return_value = None
        b = self._make({
            "w": {
                "s3_bucket": "bkt",
                "mode": "write",
                "blocksize": ["4k", "128k"],
                "threads": [1, 4],
                "iodepth": [1, 4],
            }
        })
        b._run_workloads()
        self.assertEqual(8, mock_pdsh.call_count)

    @patch("common.make_remote_dir")
    @patch("common.pdsh")
    def test_run_dir_path_structure(self, mock_pdsh, mock_mkdir):
        """Run directory follows {mode}_{bs_bytes}/threads-NNN/iodepth-MMM."""
        mock_pdsh.return_value.communicate.return_value = None
        b = self._make({
            "w": {
                "s3_bucket": "bkt",
                "mode": "write",
                "blocksize": ["4k"],
                "threads": [16],
                "iodepth": [4],
            }
        })
        b._run_workloads()
        mkdir_calls = [c.args[0] for c in mock_mkdir.call_args_list]
        self.assertTrue(
            any("write_4096/threads-016/iodepth-004" in d for d in mkdir_calls),
            f"Expected path segment not found in: {mkdir_calls}",
        )

    @patch("common.make_remote_dir")
    @patch("common.pdsh")
    def test_stat_workload_skipped(self, mock_pdsh, mock_mkdir):
        """Workloads with mode=stat must be skipped (no pdsh calls)."""
        mock_pdsh.return_value.communicate.return_value = None
        b = self._make({
            "w": {"s3_bucket": "bkt", "mode": "stat", "threads": [1], "iodepth": [1]}
        })
        b._run_workloads()
        mock_pdsh.assert_not_called()

    @patch("common.make_remote_dir")
    @patch("common.pdsh")
    def test_scalar_threads_and_iodepth_treated_as_single_value(self, mock_pdsh, mock_mkdir):
        """Scalar (non-list) threads/iodepth values produce exactly one run."""
        mock_pdsh.return_value.communicate.return_value = None
        b = self._make({
            "w": {
                "s3_bucket": "bkt",
                "mode": "write",
                "blocksize": "4k",
                "threads": 4,
                "iodepth": 2,
            }
        })
        b._run_workloads()
        self.assertEqual(1, mock_pdsh.call_count)


if __name__ == "__main__":
    unittest.main()
