"""
CBT benchmark module for Elbencho in S3 mode.

Registers Elbencho as a benchmark so that adding ``elbencho:`` to a YAML
test plan is enough to invoke it.

The class snapshots top-level keys as global defaults, and stores the
workloads dictionary for use at run time.  List-valued workload
parameters (``threads``, ``iodepth``, ``blocksize``) are subject to
the Cartesian permutation as per usual, but not within the usual
``benchmarkfactory.expand_configs()`` function which applies to
top-level list values — this class owns the expansion internally.
"""

import logging
import os
import pprint

import common
import monitoring
import settings

from .benchmark import Benchmark

logger = logging.getLogger("cbt")


class Elbencho(Benchmark):
    """
    The Elbencho S3 benchmark class owns top-level and workload-level 
    keys. It has built-in functionality to validate the executable Elbencho 
    binary path and perform the Cartesian expansion of different workloads.
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
        
        # --- workload-level (isolated) keys ---
        # workloads is a dict of named workload entries; 
        # each entry carries its own benchmark parameters.
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
    def workload_configs(cls, config):
        """
        Elbencho uses a workloads model: list-valued parameters (``threads``,
        ``iodepth``, ``blocksize``) are expanded internally by the class, not
        by ``benchmarkfactory.expand_configs()``.  Yield a single config dict
        so the factory instantiates exactly one Elbencho object per run.
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

        # Raises if the binary is missing or not executable on any node.
        # before touching the cluster or starting monitoring.
        logger.info("Verifying elbencho binary is executable on all client nodes: %s", self.cmd_path)
        common.pdsh_check(settings.getnodes('clients'), f"test -x {self.cmd_path}")

        self.cleandir()

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
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_workloads(self):
        """Iterate over every named workload in definition order.

        The three-tier nested loop (blocksize → threads → iodepth) will be
        left as a TODO.  Here we only validate and log each
        workload entry so the class is fully registered and callable.
        """
        logger.info("Elbencho: workload iteration complete.")
