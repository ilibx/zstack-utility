from kvmagent import kvmagent
from zstacklib.utils import jsonobject
from zstacklib.utils import http
from zstacklib.utils import lock
from zstacklib.utils import log
from zstacklib.utils.bash import *
from zstacklib.utils import linux
from zstacklib.utils import thread
from zstacklib.utils import lvm
from zstacklib.utils.ip import get_nic_supported_max_speed
from jinja2 import Template
import os.path
import re
import time
import traceback
from prometheus_client.core import GaugeMetricFamily,REGISTRY
from prometheus_client import start_http_server

logger = log.get_logger(__name__)

def collect_host_network_statistics():

    all_eths = bash_o("ls /sys/class/net/").split()
    virtual_eths = bash_o("ls /sys/devices/virtual/net/").split()

    interfaces = []
    for eth in all_eths:
        eth = eth.strip(' \t\n\r')
        if eth in virtual_eths: continue
        if eth == 'bonding_masters':
            continue
        elif not eth:
            continue
        else:
            interfaces.append(eth)

    all_in_bytes = 0
    all_in_packets = 0
    all_in_errors = 0
    all_out_bytes = 0
    all_out_packets = 0
    all_out_errors = 0
    for intf in interfaces:
        res = bash_o("cat /sys/class/net/{}/statistics/rx_bytes".format(intf))
        all_in_bytes += int(res)

        res = bash_o("cat /sys/class/net/{}/statistics/rx_packets".format(intf))
        all_in_packets += int(res)

        res = bash_o("cat /sys/class/net/{}/statistics/rx_errors".format(intf))
        all_in_errors += int(res)

        res = bash_o("cat /sys/class/net/{}/statistics/tx_bytes".format(intf))
        all_out_bytes += int(res)

        res = bash_o("cat /sys/class/net/{}/statistics/tx_packets".format(intf))
        all_out_packets += int(res)

        res = bash_o("cat /sys/class/net/{}/statistics/tx_errors".format(intf))
        all_out_errors += int(res)

    metrics = {
        'host_network_all_in_bytes': GaugeMetricFamily('host_network_all_in_bytes',
                                                       'Host all inbound traffic in bytes'),
        'host_network_all_in_packages': GaugeMetricFamily('host_network_all_in_packages',
                                                          'Host all inbound traffic in packages'),
        'host_network_all_in_errors': GaugeMetricFamily('host_network_all_in_errors',
                                                        'Host all inbound traffic errors'),
        'host_network_all_out_bytes': GaugeMetricFamily('host_network_all_out_bytes',
                                                        'Host all outbound traffic in bytes'),
        'host_network_all_out_packages': GaugeMetricFamily('host_network_all_out_packages',
                                                           'Host all outbound traffic in packages'),
        'host_network_all_out_errors': GaugeMetricFamily('host_network_all_out_errors',
                                                         'Host all outbound traffic errors'),
    }

    metrics['host_network_all_in_bytes'].add_metric([], float(all_in_bytes))
    metrics['host_network_all_in_packages'].add_metric([], float(all_in_packets))
    metrics['host_network_all_in_errors'].add_metric([], float(all_in_errors))
    metrics['host_network_all_out_bytes'].add_metric([], float(all_out_bytes))
    metrics['host_network_all_out_packages'].add_metric([], float(all_out_packets))
    metrics['host_network_all_out_errors'].add_metric([], float(all_out_errors))

    return metrics.values()


def collect_host_capacity_statistics():
    default_zstack_path = '/usr/local/zstack/apache-tomcat/webapps/zstack'

    zstack_env_path = os.environ.get('ZSTACK_HOME', None)
    if zstack_env_path and zstack_env_path != default_zstack_path:
        default_zstack_path = zstack_env_path

    zstack_dir = ['/var/lib/zstack', '%s/../../../' % default_zstack_path, '/opt/zstack-dvd/',
                  '/var/log/zstack', '/var/lib/mysql', '/var/lib/libvirt', '/tmp/zstack']

    metrics = {
        'zstack_used_capacity_in_bytes': GaugeMetricFamily('zstack_used_capacity_in_bytes',
                                                           'ZStack used capacity in bytes')
    }

    zstack_used_capacity = 0
    for dir in zstack_dir:
        if not os.path.exists(dir):
            continue
        cmd = "du -bs %s | awk {\'print $1\'}" % dir
        res = bash_o(cmd)
        zstack_used_capacity += int(res)

    metrics['zstack_used_capacity_in_bytes'].add_metric([], float(zstack_used_capacity))
    return metrics.values()


def collect_lvm_capacity_statistics():
    metrics = {
        'vg_size': GaugeMetricFamily('vg_size',
                                     'volume group size', None, ['vg_name']),
        'vg_avail': GaugeMetricFamily('vg_avail',
                                      'volume group and thin pool free size', None, ['vg_name']),
    }

    r, o, e = bash_roe("vgs --nolocking --noheading -oname")
    if r != 0 or len(o.splitlines()) == 0:
        return metrics.values()

    vg_names = o.splitlines()
    for name in vg_names:
        name = name.strip()
        size, avail = lvm.get_vg_size(name, False)
        metrics['vg_size'].add_metric([name], float(size))
        metrics['vg_avail'].add_metric([name], float(avail))

    return metrics.values()


def convert_raid_state_to_int(state):
    """

    :type state: str
    """
    state = state.lower()
    if state == "optimal":
        return 0
    elif state == "degraded":
        return 5
    else:
        return 100


def convert_disk_state_to_int(state):
    """

    :type state: str
    """
    state = state.lower()
    if "online" in state:
        return 0
    elif "rebuild" in state:
        return 5
    elif "failed" in state:
        return 10
    elif "unconfigured" in state:
        return 15
    else:
        return 100


def collect_raid_state():
    metrics = {
        'raid_state': GaugeMetricFamily('raid_state',
                                        'raid state', None, ['target_id']),
        'physical_disk_state': GaugeMetricFamily('physical_disk_state',
                                                 'physical disk state', None,
                                                 ['slot_number', 'disk_group']),
    }
    if bash_r("/opt/MegaRAID/MegaCli/MegaCli64 -LDInfo -LALL -aAll") != 0:
        return metrics.values()

    raid_info = bash_o("/opt/MegaRAID/MegaCli/MegaCli64 -LDInfo -LALL -aAll | grep -E 'Target Id|State'").strip().splitlines()
    target_id = state = None
    for info in raid_info:
        if "Target Id" in info:
            target_id = info.strip().strip(")").split(" ")[-1]
        else:
            state = info.strip().split(" ")[-1]
            metrics['raid_state'].add_metric([target_id], convert_raid_state_to_int(state))

    disk_info = bash_o(
        "/opt/MegaRAID/MegaCli/MegaCli64 -PDList -aAll | grep -E 'Slot Number|DiskGroup|Firmware state'").strip().splitlines()
    slot_number = state = disk_group = None
    for info in disk_info:
        if "Slot Number" in info:
            slot_number = info.strip().split(" ")[-1]
        elif "DiskGroup" in info:
            kvs = info.replace("Drive's position: ", "").split(",")
            disk_group = filter(lambda x: "DiskGroup" in x, kvs)[0]
            disk_group = disk_group.split(" ")[-1]
        else:
            state = info.strip().split(":")[-1]
            metrics['physical_disk_state'].add_metric([slot_number, disk_group], convert_disk_state_to_int(state))

    return metrics.values()


def collect_equipment_state():
    metrics = {
        'power_supply': GaugeMetricFamily('power_supply',
                                          'power supply', None, ['ps_id']),
        'ipmi_status': GaugeMetricFamily('ipmi_status', 'ipmi status', None, []),
        'physical_network_interface': GaugeMetricFamily('physical_network_interface',
                                                        'physical network interface', None,
                                                        ['interface_name', 'speed']),
    }

    r, ps_info = bash_ro("ipmitool sdr type 'power supply'")  # type: (int, str)
    if r == 0:
        for info in ps_info.splitlines():
            info = info.strip()
            ps_id = info.split("|")[0].strip().split(" ")[0]
            health = 10 if "fail" in info.lower() else 0
            metrics['power_supply'].add_metric([ps_id], health)

    metrics['ipmi_status'].add_metric([], bash_r("ipmitool mc info"))

    nics = bash_o("find /sys/class/net -type l -not -lname '*virtual*' -printf '%f\\n'").splitlines()
    if len(nics) != 0:
        for nic in nics:
            nic = nic.strip()
            status = bash_r("grep 1 /sys/class/net/%s/carrier" % nic)
            speed = str(get_nic_supported_max_speed(nic))
            metrics['physical_network_interface'].add_metric([nic, speed], status)

    return metrics.values()


kvmagent.register_prometheus_collector(collect_host_network_statistics)
kvmagent.register_prometheus_collector(collect_host_capacity_statistics)
kvmagent.register_prometheus_collector(collect_lvm_capacity_statistics)
kvmagent.register_prometheus_collector(collect_raid_state)
kvmagent.register_prometheus_collector(collect_equipment_state)


class PrometheusPlugin(kvmagent.KvmAgent):

    COLLECTD_PATH = "/prometheus/collectdexporter/start"

    @kvmagent.replyerror
    @in_bash
    def start_collectd_exporter(self, req):

        @in_bash
        def start_exporter(cmd):
            conf_path = os.path.join(os.path.dirname(cmd.binaryPath), 'collectd.conf')

            conf = '''Interval {{INTERVAL}}
FQDNLookup false

LoadPlugin syslog
LoadPlugin aggregation
LoadPlugin cpu
LoadPlugin disk
LoadPlugin interface
LoadPlugin memory
LoadPlugin network
LoadPlugin virt

<Plugin aggregation>
	<Aggregation>
		#Host "unspecified"
		Plugin "cpu"
		#PluginInstance "unspecified"
		Type "cpu"
		#TypeInstance "unspecified"

		GroupBy "Host"
		GroupBy "TypeInstance"

		CalculateNum false
		CalculateSum false
		CalculateAverage true
		CalculateMinimum false
		CalculateMaximum false
		CalculateStddev false
	</Aggregation>
</Plugin>

<Plugin cpu>
  ReportByCpu true
  ReportByState true
  ValuesPercentage true
</Plugin>

<Plugin disk>
  Disk "/^sd[a-z]$/"
  Disk "/^hd[a-z]$/"
  Disk "/^vd[a-z]$/"
  IgnoreSelected false
</Plugin>

<Plugin "interface">
{% for i in INTERFACES -%}
  Interface "{{i}}"
{% endfor -%}
  IgnoreSelected false
</Plugin>

<Plugin memory>
	ValuesAbsolute true
	ValuesPercentage false
</Plugin>

<Plugin virt>
	Connection "qemu:///system"
	RefreshInterval {{INTERVAL}}
	HostnameFormat name
    PluginInstanceFormat name
    BlockDevice "/:hd[a-z]/"
    IgnoreSelected true
</Plugin>

<Plugin network>
	Server "localhost" "25826"
</Plugin>

'''

            tmpt = Template(conf)
            conf = tmpt.render({
                'INTERVAL': cmd.interval,
                'INTERFACES': interfaces,
            })

            need_restart_collectd = False
            if os.path.exists(conf_path):
                with open(conf_path, 'r') as fd:
                    old_conf = fd.read()

                if old_conf != conf:
                    with open(conf_path, 'w') as fd:
                        fd.write(conf)
                    need_restart_collectd = True
            else:
                with open(conf_path, 'w') as fd:
                    fd.write(conf)
                need_restart_collectd = True

            cpid = linux.find_process_by_cmdline(['collectd', conf_path])
            mpid = linux.find_process_by_cmdline(['collectdmon', conf_path])

            if not cpid:
                bash_errorout('collectdmon -- -C %s' % conf_path)
            else:
                if need_restart_collectd:
                    if not mpid:
                        bash_errorout('kill -TERM %s' % cpid)
                        bash_errorout('collectdmon -- -C %s' % conf_path)
                    else:
                        bash_errorout('kill -HUP %s' % mpid)

            pid = linux.find_process_by_cmdline([cmd.binaryPath])
            if not pid:
                EXPORTER_PATH = cmd.binaryPath
                LOG_FILE = os.path.join(os.path.dirname(EXPORTER_PATH), cmd.binaryPath + '.log')
                ARGUMENTS = cmd.startupArguments
                if not ARGUMENTS:
                    ARGUMENTS = ""
                bash_errorout('chmod +x {{EXPORTER_PATH}}')
                bash_errorout("nohup {{EXPORTER_PATH}} {{ARGUMENTS}} >{{LOG_FILE}} 2>&1 < /dev/null &\ndisown")

        para = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = kvmagent.AgentResponse()

        eths = bash_o("ls /sys/class/net").split()
        interfaces = []
        for eth in eths:
            eth = eth.strip(' \t\n\r')
            if eth == 'lo': continue
            if eth == 'bonding_masters': continue
            elif eth.startswith('vnic'): continue
            elif eth.startswith('outer'): continue
            elif eth.startswith('br_'): continue
            elif not eth: continue
            else:
                interfaces.append(eth)

        for cmd in para.cmds:
            start_exporter(cmd)

        self.install_iptables()

        return jsonobject.dumps(rsp)

    @in_bash
    @lock.file_lock('/run/xtables.lock')
    def install_iptables(self):
        def install_iptables_port(rules, port):
            needle = '-A INPUT -p tcp -m tcp --dport %d' % port
            drules = [ r.replace("-A ", "-D ") for r in rules if needle in r ]
            for rule in drules:
                bash_r("iptables -w %s" % rule)

            bash_r("iptables -w -I INPUT -p tcp --dport %s -j ACCEPT" % port)

        rules = bash_o("iptables -w -S INPUT").splitlines()
        install_iptables_port(rules, 7069)
        install_iptables_port(rules, 9100)
        install_iptables_port(rules, 9103)

    def install_colletor(self):
        class Collector(object):
            def collect(self):
                try:
                    ret = []
                    for c in kvmagent.metric_collectors:
                        ret.extend(c())

                    return ret
                except Exception as e:
                    content = traceback.format_exc()
                    err = '%s\n%s\n' % (str(e), content)
                    logger.warn(err)
                    return []

        REGISTRY.register(Collector())

    def start(self):
        http_server = kvmagent.get_http_server()
        http_server.register_async_uri(self.COLLECTD_PATH, self.start_collectd_exporter)

        self.install_colletor()
        start_http_server(7069)

    def stop(self):
        pass
