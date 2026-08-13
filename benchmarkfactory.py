import copy
import itertools

import settings
from benchmark.cephtestrados import CephTestRados
from benchmark.cosbench import Cosbench
from benchmark.elbencho import Elbencho
from benchmark.fio import Fio
from benchmark.getput import Getput
from benchmark.hsbench import Hsbench
from benchmark.kvmrbdfio import KvmRbdFio
from benchmark.librbdfio import LibrbdFio
from benchmark.nullbench import Nullbench
from benchmark.radosbench import Radosbench
from benchmark.rawfio import RawFio
from benchmark.rbdfio import RbdFio

BENCHMARK_CLASSES = {
    'nullbench': Nullbench,
    'radosbench': Radosbench,
    'fio': Fio,
    'hsbench': Hsbench,
    'rbdfio': RbdFio,
    'kvmrbdfio': KvmRbdFio,
    'rawfio': RawFio,
    'librbdfio': LibrbdFio,
    'cosbench': Cosbench,
    'cephtestrados': CephTestRados,
    'getput': Getput,
    'elbencho': Elbencho,
}


def get_all(archive, cluster, iteration):
    for benchmark, config in sorted(settings.benchmarks.items()):
        default = {"benchmark": benchmark,
                   "iteration": iteration}
        bclass = BENCHMARK_CLASSES.get(benchmark)
        # If the benchmark uses workloads, we use its internal method
        # to generate the configurations rather than going down the usual
        # expand_configs() method which is deprecated (we want to move towards
        # workloads)
        if bclass is not None and hasattr(bclass, 'workload_configs'):
            configs = bclass.workload_configs(config)
        else:
            configs = expand_configs(config)

        for current in configs:
            current.update(default)
            yield get_object(archive, cluster, benchmark, current)


def expand_configs(config):
    """
    return all parameter combinations for config
    config: dict - list of params
    iterate over all top-level lists in config

    Deprecated: we're moving towards workloads,
    which is a different code path -- see get_all()
    """
    cycle_over_lists = []
    cycle_over_names = []
    default = {}

    for param, value in list(config.items()):
        # acceptable applies to benchmark as a whole, no need to it to
        # the set for permutation
        if param == 'acceptable':
            default[param] = value
        elif isinstance(value, list):
            cycle_over_lists.append(value)
            cycle_over_names.append(param)
        else:
            default[param] = value

    for permutation in itertools.product(*cycle_over_lists):
        current = copy.deepcopy(default)
        current.update(list(zip(cycle_over_names, permutation)))
        yield current

def get_object(archive, cluster, benchmark, bconfig):
    try:
        return BENCHMARK_CLASSES[benchmark](archive, cluster, bconfig)
    except KeyError:
        return None
