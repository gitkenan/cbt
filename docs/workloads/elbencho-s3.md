# Elbencho S3 — running with CBT

This guide covers running the Elbencho S3 benchmark end-to-end via CBT; writing the test plan
YAML, executing the run, and verifying results. A ready-to-edit example YAML lives at
[`example/wip-elbencho/elbencho_ex.yaml`](../../example/wip-elbencho/elbencho_ex.yaml).

## Prerequisites

- `elbencho` installed on all client nodes
- A running Ceph RGW endpoint and an S3 user with read/write access
- An existing S3 bucket (or set `mkdirs: True` on the first write workload to create one)
- CBT's standard pdsh SSH access from the head node to all client nodes

## Installing elbencho

On each client node, install from the GitHub releases page. For RHEL/CentOS/Rocky:

```bash
# Check the latest release at https://github.com/breuner/elbencho/releases
ELBENCHO_VERSION=3.1-9
curl -LO https://github.com/breuner/elbencho/releases/download/v${ELBENCHO_VERSION}/elbencho-${ELBENCHO_VERSION}-1.x86_64.rpm
rpm -ivh elbencho-${ELBENCHO_VERSION}-1.x86_64.rpm
```

For Debian/Ubuntu:

```bash
ELBENCHO_VERSION=3.1-9
curl -LO https://github.com/breuner/elbencho/releases/download/v${ELBENCHO_VERSION}/elbencho-${ELBENCHO_VERSION}-1.amd64.deb
dpkg -i elbencho-${ELBENCHO_VERSION}-1.amd64.deb
```

Verify the install and confirm S3 support is included:

```bash
elbencho --version
# Look for "s3" in the "Included optional build features" line
```

To install on all CBT client nodes at once via pdsh:

```bash
pdsh -w <client1>,<client2> 'rpm -ivh /path/to/elbencho.rpm'
```

## Finding your RGW endpoint and credentials

Run these on your Ceph admin/mon node.

**Which port is RGW actually listening on:**

```bash
ss -tlnp | grep radosgw
```

This is the port to use in the `url=` field of `auth.config`. Common values are `8000` or `8080`.
Note: `ceph config get client.rgw rgw_frontends` may show a configured port that differs from
what is actually bound — always confirm with `ss`.

**Which RGW users exist:**

```bash
radosgw-admin user list
```

**Get the access key and secret for a user:**

```bash
radosgw-admin user info --uid=<username>
```

The output contains a `keys` array. Use the `access_key` and `secret_key` from the entry
whose `user` matches `<username>` (not a subuser). Example output:

```json
"keys": [
    {
        "user": "cbt",
        "access_key": "<your-access-key>",
        "secret_key": "<your-secret-key",
        "active": true
    }
]
```

**Create a new RGW user if needed:**

```bash
radosgw-admin user create --uid=cbt --display-name=cbt --access-key=<key> --secret=<secret>
```

**Verify the endpoint responds:**

```bash
curl -v http://<rgw-host>:<port>/
# Expect HTTP 200 with a ListAllMyBucketsResult XML body
```

## Test plan YAML

See the [`example/wip-elbencho/elbencho_ex.yaml`](../../example/wip-elbencho/elbencho_ex.yaml) for
an example as to what a working YAML would look like.

Replace `<your-access-key>`, `<your-secret-key>`, and the RGW URL with your cluster's values.

> **`mkdirs: True`** tells elbencho to create the S3 bucket before writing. Set it on the
> first write workload only. If the bucket already exists and your RGW is slow to respond to
> bucket-create requests, omit `mkdirs` (or set it `False`) and pre-create the bucket manually:
> ```bash
> radosgw-admin bucket create --bucket=cbt-benchmark --uid=cbt
> ```

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
> files into CBT's standard report pipeline is the work of Stories 4 and 5. To verify that 
> the data is there from the CLI, it suffices to use `cat <file-name>`.
