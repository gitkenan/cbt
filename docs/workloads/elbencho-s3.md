# Elbencho S3 — running with CBT

This guide covers running the Elbencho S3 benchmark end-to-end via CBT; writing the test plan
YAML, executing the run, and verifying results. A ready-to-edit example YAML lives at
[`example/wip-elbencho/elbencho_ex.yaml`](../../example/wip-elbencho/elbencho_ex.yaml).

## Prerequisites

- `elbencho` installed on all client nodes
- A running Ceph RGW endpoint and an S3 user with read/write access
- An existing S3 bucket (or set `mkdirs: True` on the first write workload to create one)

## Test plan YAML

```yaml
cluster:
  user: 'cbt'
  head: 'mon1'
  clients: ['client1']
  osds: ['osd1', 'osd2', 'osd3']
  rgws: ['osd1', 'osd2', 'osd3']
  osds_per_node: 1
  conf_file: '/etc/ceph/ceph.conf'
  iterations: 1
  use_existing: True
  clusterid: 'ceph'
  tmp_dir: '/tmp/cbt'

benchmarks:
  elbencho:
    cmd_path: '/usr/local/bin/elbencho'
    auth:
      config: access_key=<your-access-key>;secret_key=<your-secret-key>;url=http://192.168.110.51:8000;retry=9

    workloads:
      write_small:
        s3_bucket: 'cbt-benchmark'
        mode: 'write'
        mkdirs: True
        threads: [1, 4]
        iodepth: [1, 4]
        blocksize: ['4k', '128k']
        size: '1g'
        num_objects: 100
        duration: 30

      read_small:
        s3_bucket: 'cbt-benchmark'
        mode: 'read'
        threads: [1, 4]
        iodepth: [1, 4]
        blocksize: ['4k', '128k']
        size: '1g'
        num_objects: 100
        duration: 30
```

Replace `<your-access-key>`, `<your-secret-key>`, and the RGW URL with your cluster's values.

## Running

```bash
python3 cbt.py --archive /tmp/cbt-results example/wip-elbencho/elbencho_ex.yaml
```

CBT will:

1. Verify the elbencho binary is executable on all client nodes via `pdsh`
2. Iterate the three-tier loop (`blocksize → threads → iodepth`) for each workload in definition order
3. Fan out one elbencho process per client node via `pdsh` for each combination
4. Sync results back to the archive directory when complete

The expected log output for each run cell looks like:

```
INFO  - Elbencho [write_small] bs=128k threads=4 iodepth=4 → /tmp/cbt/00000000/Elbencho/write_131072/threads-004/iodepth-004
DEBUG - CheckedPopen ... pdsh ... /usr/local/bin/elbencho --write --threads 4 --block 128k --iodepth 4 \
          --size 1g --files 100 --timelimit 30 \
          --s3endpoints http://192.168.110.51:8000 --s3key <key> --s3secret <secret> \
          --s3region default \
          --resfile /tmp/cbt/00000000/Elbencho/write_131072/threads-004/iodepth-004/result.csv \
          s3://cbt-benchmark
INFO  - Elbencho: all workloads complete.
```

With 2 blocksizes × 2 thread values × 2 iodepth values × 2 workloads (write + read), this
configuration produces **16 run cells** and takes approximately 16 × 30s = ~8 minutes end-to-end.

## Expected result files

Each run cell produces a `result.csv` under the corresponding directory segment:

```
/tmp/cbt-results/results/00000000/<id>/
  write_4096/threads-001/iodepth-001/result.csv
  write_4096/threads-001/iodepth-004/result.csv
  ...
  write_131072/threads-004/iodepth-004/result.csv
  read_4096/threads-001/iodepth-001/result.csv
  ...
  read_131072/threads-004/iodepth-004/result.csv
```

A populated `result.csv` for the highest-load write cell (`128k`, 4 threads, iodepth 4)
looks like:

```
ISO DATE: 2026-08-12T16:27:22+0100
COMMAND LINE: "/usr/local/bin/elbencho" "--write" "--threads" "4" "--block" "128k" ...

OPERATION   RESULT TYPE         FIRST DONE   LAST DONE
=========== ================    ==========   =========
WRITE       Elapsed time     :     30.140s     30.196s
            IOPS             :         291         291
            Throughput MiB/s :          36          36
            Total MiB        :        1101        1101
```

> **Note**: `result.csv` is elbencho's native CSV result format. Parsing and plotting these
> files into CBT's standard report pipeline is the work of Stories 4 and 5.
