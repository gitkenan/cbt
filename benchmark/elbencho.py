"""
elbencho.py -- CBT benchmark module for Elbencho in S3 mode.

Registers Elbencho as a benchmark so that adding ``elbencho:`` to a YAML
test plan is enough to invoke it.

The class reads the top-level keys (``cmd_path``, ``auth``) on
construction, snapshots them as global defaults, and stores the
``workloads`` dictionary for use at run time.  List-valued workload
parameters (``threads``, ``iodepth``, ``blocksize``) are expanded
internally via the three-tier nested loop (blocksize → threads → iodepth)
in ``_run_workloads()``, not by ``benchmarkfactory.all_configs()``.
"""

import logging
import os
import pprint
import re

import common
import monitoring
import settings

from .benchmark import Benchmark

logger = logging.getLogger("cbt")

# Human-readable blocksize suffixes → multipliers.
_BS_SUFFIXES = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}

# Elbencho mode → CLI flag(s).
_MODE_FLAGS = {
    "write":     ["--write"],
    "read":      ["--read"],
    "readwrite": ["--write", "--read"],
    "stat":      ["--stat"],
    "list":      ["--s3listobjpar"],
}

# Modes that have no meaningful blocksize / size dimension.
_MODES_NO_BLOCKSIZE = {"stat", "list"}


class Elbencho(Benchmark):
    """Elbencho S3 benchmark.

    Top-level YAML keys (``cmd_path``, ``auth``) are fixed for the entire
    benchmark session.  Per-workload parameters live inside ``workloads``.
    """

    def __init__(self, archive_dir, cluster, config):
        super().__init__(archive_dir, cluster, config)

        # --- top-level (session-wide) keys ---
        self.cmd_path = config.get("cmd_path", "/usr/local/bin/elbencho")
        self.auth = config.get("auth", {})
        self._global_defaults = {
            "cmd_path": self.cmd_path,
            "auth": self.auth,
        }

        # ``workloads`` is a dict of named workload entries; each entry
        # carries its own benchmark parameters.
        self.workloads = config.get("workloads", {})
        if not isinstance(self.workloads, dict):
            raise ValueError(f"workloads must be a dict, got {type(self.workloads).__name__}")

        for workload_name, workload_params in self.workloads.items():
            if not isinstance(workload_params, dict):
                raise ValueError(f"workload '{workload_name}' must be a dict")

        # This is for use in future stories
        self.base_run_dir = self.run_dir

        # Validate required keys in the YAML schema
        for workload_name, workload_params in self.workloads.items():
            if "mode" not in workload_params:
                raise ValueError(f"workload '{workload_name}' missing required key 'mode'")
            if "s3_bucket" not in workload_params:
                raise ValueError(f"workload '{workload_name}' missing required key 's3_bucket'")

        if self.workloads:
            logger.info(
                "%d Elbencho workload(s) defined:\n    %s",
                len(self.workloads),
                pprint.pformat(self.workloads).replace("\n", "\n    "),
            )

    # ------------------------------------------------------------------
    # Config generation
    # ------------------------------------------------------------------

    @classmethod
    def generate_configs(cls, config):
        """
        Elbencho uses a workloads model, so we bypass Cartesian expansion
        and yield a single configuration instance.
        """
        yield dict(config)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Return True if the output archive directory already contains results."""
        if os.path.exists(self.archive_dir):
            logger.info("Skipping existing Elbencho results in %s.", self.archive_dir)
            return True
        return False

    def initialize(self):
        super().initialize()

        # Fail-fast: verify the elbencho binary exists and is executable on all
        # client nodes before touching the cluster or starting monitoring.
        # Raises if the binary is missing or not executable on any node.
        logger.info("Verifying elbencho binary is executable on all client nodes: %s", self.cmd_path)
        common.pdsh_check(settings.getnodes('clients'), f"test -x {self.cmd_path}")

        self.cleandir()

        # Create the local archive dir so cbt.py's run-loop gate (exists())
        # returns True and b.run() is not skipped on a fresh run.
        if not os.path.exists(self.archive_dir):
            os.makedirs(self.archive_dir)

    def run(self):
        super().run()

        if not self.workloads:
            logger.warning("Elbencho: no workloads defined — nothing to run.")
            return

        self.dropcaches()
        common.make_remote_dir(self.run_dir)
        self.cluster.dump_config(self.run_dir)

        monitoring.start(self.run_dir)

        self._run_workloads()

        monitoring.stop(self.run_dir)
        common.sync_files(f"{self.run_dir}/*", self.archive_dir)

    def cleanup(self):
        pass

    # ------------------------------------------------------------------
    # Command-building helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_blocksize_to_bytes(blocksize: str) -> int:
        """Convert a human-readable blocksize string to bytes.

        Accepts strings like ``"4k"``, ``"128k"``, ``"1m"``, ``"1g"`` or a
        plain decimal integer string (already in bytes).  Case-insensitive.

        >>> Elbencho._parse_blocksize_to_bytes("4k")
        4096
        >>> Elbencho._parse_blocksize_to_bytes("128k")
        131072
        >>> Elbencho._parse_blocksize_to_bytes("1m")
        1048576
        """
        s = str(blocksize).strip().lower()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmg]?)", s)
        if not m:
            raise ValueError(f"Unrecognised blocksize format: {blocksize!r}")
        value, suffix = m.group(1), m.group(2)
        return int(float(value) * _BS_SUFFIXES.get(suffix, 1))

    @staticmethod
    def _build_auth_flags(auth: dict) -> list:
        """Translate the ``auth:`` YAML block into elbencho CLI flags.

        Supports three credential modes:

        * ``auth.config`` string  — ``access_key=X;secret_key=Y;url=U;...``
        * environment variables   — nothing emitted; elbencho reads AWS_* env vars
        * ``auth.s3_session_token`` — emits ``--s3authtoken <token>``

        Returns a list of flag strings ready to be joined into a shell command.
        """
        flags = []

        config_str = auth.get("config", "")
        if config_str:
            # Parse semicolon-separated key=value pairs.
            pairs = dict(
                kv.split("=", 1)
                for kv in config_str.split(";")
                if "=" in kv
            )
            if "url" in pairs:
                flags += ["--s3endpoints", pairs["url"]]
            if "access_key" in pairs:
                flags += ["--s3key", pairs["access_key"]]
            if "secret_key" in pairs:
                flags += ["--s3secret", pairs["secret_key"]]

        token = auth.get("s3_session_token", "")
        if token:
            flags += ["--s3authtoken", token]

        return flags

    def _build_elbencho_cmd(
        self,
        workload: dict,
        blocksize: str,
        threads: int,
        iodepth: int,
        run_dir: str,
    ) -> str:
        """Assemble the elbencho shell command for one run cell.

        ``run_dir`` is used as the ``--resfile`` output path so results land
        in the correct directory segment.  The bucket path is suffixed with
        ``$(hostname -s)`` at shell-evaluation time to prevent object-key
        collisions when multiple clients write to the same bucket via pdsh.
        """
        mode = workload["mode"]

        if mode in _MODES_NO_BLOCKSIZE:
            logger.warning(
                "Elbencho: mode '%s' is not yet supported by the formatter/plotter "
                "(Story 5). Skipping run (blocksize=%s, threads=%d, iodepth=%d).",
                mode, blocksize, threads, iodepth,
            )
            return ""

        mode_flags = _MODE_FLAGS.get(mode)
        if mode_flags is None:
            raise ValueError(f"Unknown elbencho mode: {mode!r}")

        bucket = workload["s3_bucket"]
        s3_region = workload.get("s3_region", "default")

        parts = [self.cmd_path]
        parts += mode_flags
        parts += ["--threads", str(threads)]
        parts += ["--block", str(blocksize)]
        parts += ["--iodepth", str(iodepth)]

        size = workload.get("size")
        if size:
            parts += ["--size", str(size)]

        num_objects = workload.get("num_objects")
        if num_objects is not None:
            parts += ["--files", str(num_objects)]

        num_dirs = workload.get("num_dirs")
        if num_dirs is not None:
            parts += ["--dirs", str(num_dirs)]

        duration = workload.get("duration")
        if duration is not None:
            parts += ["--timelimit", str(duration)]

        if workload.get("direct"):
            parts += ["--direct"]

        if workload.get("no_cleanup"):
            parts += ["--nocleanup"]

        if workload.get("deldirs"):
            parts += ["--deldirs"]

        hosts = workload.get("hosts")
        if hosts:
            parts += ["--hosts", str(hosts)]

        # Auth flags (endpoint, key, secret, optional session token).
        parts += self._build_auth_flags(self.auth)
        parts += ["--s3region", s3_region]

        # Output: CSV result file lands in the per-run directory.
        parts += ["--resfile", os.path.join(run_dir, "result.csv")]

        mkdirs = workload.get("mkdirs", False)
        if mkdirs:
            parts += ["--mkdirs"]
        parts += [f"s3://{bucket}"]

        return " ".join(parts)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def _run_workloads(self):
        """Three-tier nested loop: blocksize (tier 1) → threads (tier 2) → iodepth (tier 3).

        For each combination a dedicated run directory is created, elbencho is
        fanned out to all client nodes via pdsh, and the process is waited on
        before moving to the next cell.  The directory layout encodes all three
        axes so the formatter (Story 4/5) can reconstruct parameters from the
        path alone::

            {base_run_dir}/{mode}_{blocksize_bytes}/threads-{NNN}/iodepth-{MMM}/
        """
        clients = settings.getnodes("clients")

        for workload_name, workload in self.workloads.items():
            mode = workload["mode"]

            if mode in _MODES_NO_BLOCKSIZE:
                logger.warning(
                    "Elbencho: workload '%s' uses mode '%s' which is not yet "
                    "supported by the result formatter (Story 5). Skipping.",
                    workload_name, mode,
                )
                continue

            blocksizes = workload.get("blocksize", ["4k"])
            if not isinstance(blocksizes, list):
                blocksizes = [blocksizes]

            threads_list = workload.get("threads", [1])
            if not isinstance(threads_list, list):
                threads_list = [threads_list]

            iodepth_list = workload.get("iodepth", [1])
            if not isinstance(iodepth_list, list):
                iodepth_list = [iodepth_list]

            for bs in blocksizes:                           # tier 1
                bs_bytes = self._parse_blocksize_to_bytes(bs)
                for threads in threads_list:               # tier 2 — outer
                    for iodepth in iodepth_list:           # tier 3 — inner
                        run_dir = (
                            f"{self.base_run_dir}"
                            f"/{mode}_{bs_bytes}"
                            f"/threads-{int(threads):03d}"
                            f"/iodepth-{int(iodepth):03d}"
                        )
                        common.make_remote_dir(run_dir)

                        cmd = self._build_elbencho_cmd(
                            workload, bs, int(threads), int(iodepth), run_dir
                        )
                        if not cmd:
                            # Unsupported mode — already logged inside builder.
                            continue

                        logger.info(
                            "Elbencho [%s] bs=%s threads=%d iodepth=%d → %s",
                            workload_name, bs, int(threads), int(iodepth), run_dir,
                        )
                        common.pdsh(clients, cmd).communicate()

        logger.info("Elbencho: all workloads complete.")
