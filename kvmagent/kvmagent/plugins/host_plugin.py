'''

@author: frank
'''
import base64
import copy
import hashlib
import platform
from kvmagent import kvmagent
from kvmagent.plugins import vm_plugin
from kvmagent.plugins.imagestore import ImageStoreClient
from zstacklib.utils import jsonobject
from zstacklib.utils import http
from zstacklib.utils import lock
from zstacklib.utils import log
from zstacklib.utils import shell
from zstacklib.utils import sizeunit
from zstacklib.utils import linux
from zstacklib.utils import xmlobject
from zstacklib.utils.bash import *
from zstacklib.utils.report import Report
from zstacklib.utils import iptables
from zstacklib.utils import thread
from zstacklib.utils.ip import get_nic_supported_max_speed
import os
import os.path
import re
import time
import uuid
import tempfile

IS_AARCH64 = platform.machine() == 'aarch64'
GRUB_FILES = ["/boot/grub2/grub.cfg", "/boot/grub/grub.cfg", "/etc/grub2-efi.cfg", "/etc/grub-efi.cfg"]

class ConnectResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(ConnectResponse, self).__init__()
        self.iptablesSucc = None

class HostCapacityResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(HostCapacityResponse, self).__init__()
        self.cpuNum = None
        self.cpuSpeed = None
        self.usedCpu = None
        self.totalMemory = None
        self.usedMemory = None
        self.cpuSockets = None

class HostFactResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(HostFactResponse, self).__init__()
        self.osDistribution = None
        self.osVersion = None
        self.osRelease = None
        self.qemuImgVersion = None
        self.libvirtVersion = None
        self.hvmCpuFlag = None
        self.cpuModelName = None
        self.systemSerialNumber = None

class SetupMountablePrimaryStorageHeartbeatCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(SetupMountablePrimaryStorageHeartbeatCmd, self).__init__()
        self.heartbeatFilePaths = None
        self.heartbeatInterval = None

class SetupMountablePrimaryStorageHeartbeatResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(SetupMountablePrimaryStorageHeartbeatResponse, self).__init__()

class PingResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(PingResponse, self).__init__()
        self.hostUuid = None

class GetUsbDevicesRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(GetUsbDevicesRsp, self).__init__()
        self.usbDevicesInfo = None

class StartUsbRedirectServerRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(StartUsbRedirectServerRsp, self).__init__()
        self.port = None

class StopUsbRedirectServerRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(StopUsbRedirectServerRsp, self).__init__()

class CheckUsbServerPortRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(CheckUsbServerPortRsp, self).__init__()
        self.uuids = []

class ReportDeviceEventCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(ReportDeviceEventCmd, self).__init__()
        self.hostUuid = None

class UpdateHostOSCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(UpdateHostOSCmd, self).__init__()
        self.hostUuid = None
        self.excludePackages = None

class UpdateHostOSRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(UpdateHostOSRsp, self).__init__()

class UpdateDependencyCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(UpdateDependencyCmd, self).__init__()
        self.hostUuid = None

class UpdateDependencyRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(UpdateDependencyRsp, self).__init__()

class GetXfsFragDataRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(GetXfsFragDataRsp, self).__init__()
        self.fsType = None
        self.hostFrag = None
        self.volumeFragMap = {}

class EnableHugePageRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(EnableHugePageRsp, self).__init__()

class DisableHugePageRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(DisableHugePageRsp, self).__init__()

class GetHostNetworkBongdingResponse(kvmagent.AgentResponse):
    bondings = None  # type: list[HostNetworkBondingInventory]
    nics = None  # type: list[HostNetworkInterfaceInventory]

    def __init__(self):
        super(GetHostNetworkBongdingResponse, self).__init__()
        self.bondings = None
        self.nics = None


class HostNetworkBondingInventory(object):
    slaves = None  # type: list(HostNetworkInterfaceInventory)

    def __init__(self, bondingName=None):
        super(HostNetworkBondingInventory, self).__init__()
        self.bondingName = bondingName
        self.mode = None
        self.xmitHashPolicy = None
        self.miiStatus = None
        self.mac = None
        self.ipAddresses = None
        self.miimon = None
        self.allSlavesActive = None
        self.slaves = None
        self._init_from_name()

    def _init_from_name(self):
        if self.bondingName is None:
            return
        self.mode = bash_o("cat /sys/class/net/%s/bonding/mode" % self.bondingName).strip()
        self.xmitHashPolicy = bash_o("cat /sys/class/net/%s/bonding/xmit_hash_policy" % self.bondingName).strip()
        self.miiStatus = bash_o("cat /sys/class/net/%s/bonding/mii_status" % self.bondingName).strip()
        self.mac = bash_o("cat /sys/class/net/%s/address" % self.bondingName).strip()
        self.ipAddresses = [x.strip() for x in
                          bash_o("ip -o a show %s | grep 'inet ' | awk '{print $4}'" % self.bondingName).splitlines()]
        if len(self.ipAddresses) == 0:
            r, master = bash_ro("cat /sys/class/net/%s/master/ifindex" % self.bondingName)
            if r == 0:
                self.ipAddresses = [x.strip() for x in bash_o(
                    "ip -o a list | grep '^%s: ' | grep 'inet ' | awk '{print $4}'" % master.strip()).splitlines()]
        self.miimon = bash_o("cat /sys/class/net/%s/bonding/miimon" % self.bondingName).strip()
        self.allSlavesActive = bash_o(
            "cat /sys/class/net/%s/bonding/all_slaves_active" % self.bondingName).strip() == "0"
        self.slaves = []
        slave_names = bash_o("cat /sys/class/net/%s/bonding/slaves" % self.bondingName).strip().split(" ")
        if len(slave_names) == 0:
            return

        for name in slave_names:
            self.slaves.append(HostNetworkInterfaceInventory(name))

    def _to_dict(self):
        to_dict = self.__dict__
        for k in to_dict.keys():
            if k == "slaves":
                v = copy.deepcopy(to_dict[k])
                to_dict[k] = [i.__dict__ for i in v]
        return to_dict


class HostNetworkInterfaceInventory(object):
    def __init__(self, name=None):
        super(HostNetworkInterfaceInventory, self).__init__()
        self.interfaceName = name
        self.speed = None
        self.slaveActive = None
        self.carrierActive = None
        self.mac = None
        self.ipAddresses = None
        self.interfaceType = None
        self.master = None
        self._init_from_name()

    def _init_from_name(self):
        if self.interfaceName is None:
            return
        self.speed = get_nic_supported_max_speed(self.interfaceName)
        self.carrierActive = bash_o("cat /sys/class/net/%s/carrier" % self.interfaceName).strip() == "1"
        self.mac = bash_o("cat /sys/class/net/%s/address" % self.interfaceName).strip()
        self.ipAddresses = [x.strip() for x in
                          bash_o("ip -o a show %s | grep 'inet ' | awk '{print $4}'" % self.interfaceName).splitlines()]

        r, master = bash_ro("cat /sys/class/net/%s/master/ifindex" % self.interfaceName)
        if r == 0 and master.strip() != "":
            self.master = bash_o("ip link | grep -E '^%s: ' | awk '{print $2}'" % master.strip()).strip().strip(":")
        if len(self.ipAddresses) == 0:
            if r == 0:
                self.ipAddresses = [x.strip() for x in bash_o(
                    "ip -o a list | grep '^%s: ' | grep 'inet ' | awk '{print $4}'" % master.strip()).splitlines()]
        if self.master is None:
            self.interfaceType = "noMaster"
        elif len(bash_o("ip link show type bond_slave %s" % self.interfaceName).strip()) > 0:
            self.interfaceType = "bondingSlave"
            self.slaveActive = self.interfaceName in bash_o("cat /sys/class/net/%s/bonding/active_slave" % self.master)
        else:
            self.interfaceType = "bridgeSlave"

    def _to_dict(self):
        to_dict = self.__dict__
        return to_dict

class GetPciDevicesCmd(kvmagent.AgentCommand):
    def __init__(self):
        super(GetPciDevicesCmd, self).__init__()
        self.filterString = None
        self.enableIommu = True

class GetPciDevicesResponse(kvmagent.AgentResponse):
    def __init__(self):
        super(GetPciDevicesResponse, self).__init__()
        self.pciDevicesInfo = []
        self.hostIommuStatus = False

class CreatePciDeviceRomFileCommand(kvmagent.AgentCommand):
    def __init__(self):
        super(CreatePciDeviceRomFileCommand, self).__init__()
        self.specUuid = None
        self.romContent = None
        self.romMd5sum = None

class CreatePciDeviceRomFileRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(CreatePciDeviceRomFileRsp, self).__init__()

class GenerateSriovPciDevicesCommand(kvmagent.AgentCommand):
    def __init__(self):
        super(GenerateSriovPciDevicesCommand, self).__init__()
        self.pciDeviceAddress = None
        self.virtPartNum = None
        self.reSplite = False

class GenerateSriovPciDevicesRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(GenerateSriovPciDevicesRsp, self).__init__()

class UngenerateSriovPciDevicesCommand(kvmagent.AgentCommand):
    def __init__(self):
        super(UngenerateSriovPciDevicesCommand, self).__init__()
        self.pciDeviceAddress = None

class UngenerateSriovPciDevicesRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(UngenerateSriovPciDevicesRsp, self).__init__()

class GenerateVfioMdevDevicesCommand(kvmagent.AgentCommand):
    def __init__(self):
        super(GenerateVfioMdevDevicesCommand, self).__init__()
        self.pciDeviceAddress = None
        self.mdevSpecTypeId = None
        self.mdevUuids = None

class GenerateVfioMdevDevicesRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(GenerateVfioMdevDevicesRsp, self).__init__()
        self.mdevUuids = []

class UngenerateVfioMdevDevicesCommand(kvmagent.AgentCommand):
    def __init__(self):
        super(UngenerateVfioMdevDevicesCommand, self).__init__()
        self.pciDeviceAddress = None
        self.mdevSpecTypeId = None

class UngenerateVfioMdevDevicesRsp(kvmagent.AgentResponse):
    def __init__(self):
        super(UngenerateVfioMdevDevicesRsp, self).__init__()

class PciDeviceTO(object):
    def __init__(self):
        self.name = ""
        self.description = ""
        self.vendorId = ""
        self.deviceId = ""
        self.subvendorId = ""
        self.subdeviceId = ""
        self.pciDeviceAddress = ""
        self.parentAddress = ""
        self.type = ""
        self.virtStatus = ""
        self.maxPartNum = "0"
        self.ramSize = ""
        self.mdevSpecifications = []

# moved from vm_plugin to host_plugin
class UpdateConfigration(object):
    def __init__(self):
        self.path = None
        self.enableIommu = None

    def executeCmdOnFile(self, shellCmd):
        return bash_roe("%s %s" % (shellCmd, self.path))

    def updateHostIommu(self):
        r_on, o_on, e_on = self.executeCmdOnFile("grep -E 'intel_iommu(\ )*=(\ )*on'")
        r_off, o_off, e_off = self.executeCmdOnFile("grep -E 'intel_iommu(\ )*=(\ )*off'")
        r_modprobe_blacklist, o_modprobe_blacklist, e_modprobe_blacklist = self.executeCmdOnFile("grep -E 'modprobe.blacklist(\ )*='")
        #When iommu has not changed,  No need to update /etc/default/grub
        if self.enableIommu is False:
            if r_on != 0 and r_off != 0 and r_modprobe_blacklist != 0:
                return True, None
        elif self.enableIommu is True:
            if r_on ==0 and r_off != 0 and r_modprobe_blacklist == 0:
                return True,None

        if r_on == 0:
            r, o, e = self.executeCmdOnFile( "sed -i '/GRUB_CMDLINE_LINUX/s/[[:blank:]]*intel_iommu[[:blank:]]*=[[:blank:]]*on//g'")
            if r != 0:
                return False, "%s %s" % (e, o)
        if r_off == 0:
            r, o, e = self.executeCmdOnFile("sed -i '/GRUB_CMDLINE_LINUX/s/[[:blank:]]*intel_iommu[[:blank:]]*=[[:blank:]]*off//g'")
            if r != 0:
                return False, "%s %s" % (e, o)
        if r_modprobe_blacklist == 0:
            r, o, e = self.executeCmdOnFile("grep -E '[[:blank:]]*modprobe.blacklist[[:blank:]]*=[[:blank:]]*[[:graph:]]*\"$'")
            if r == 0:
                r, o, e = self.executeCmdOnFile("sed -i '/GRUB_CMDLINE_LINUX/s/[[:blank:]]*modprobe.blacklist[[:blank:]]*=[[:blank:]]*[[:graph:]]*\"$/\"/g'")
                if r != 0:
                    return False, "%s %s" % (e, o)
            else:
                r, o, e = self.executeCmdOnFile("sed -i '/GRUB_CMDLINE_LINUX/s/[[:blank:]]*modprobe.blacklist[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g'")
                if r != 0:
                    return False, "%s %s" % (e, o)

        if self.enableIommu is True:
            r, o, e = self.executeCmdOnFile("sed -i '/GRUB_CMDLINE_LINUX/s/\"$/ intel_iommu=on modprobe.blacklist=snd_hda_intel,amd76x_edac,vga16fb,nouveau,rivafb,nvidiafb,rivatv,amdgpu,radeon\"/g'")
            if r != 0:
                return False, "%s %s" % (e, o)

        return True, None

    def updateGrubConfig(self):
        linux.updateGrubFile("grep -E 'intel_iommu(\ )*=(\ )*on'", "sed -i '/^[[:space:]]*linux/s/[[:blank:]]*intel_iommu[[:blank:]]*=[[:blank:]]*on//g'", GRUB_FILES)
        linux.updateGrubFile("grep -E 'intel_iommu(\ )*=(\ )*off'", "sed -i '/^[[:space:]]*linux/s/[[:blank:]]*intel_iommu[[:blank:]]*=[[:blank:]]*off//g'", GRUB_FILES)
        linux.updateGrubFile("grep -E 'modprobe.blacklist(\ )*='", "sed -i '/^[[:space:]]*linux/s/[[:blank:]]*modprobe.blacklist[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g'", GRUB_FILES)
        if self.enableIommu is True:
            linux.updateGrubFile(None, "sed -i '/^[[:space:]]*linux/s/$/ intel_iommu=on modprobe.blacklist=snd_hda_intel,amd76x_edac,vga16fb,nouveau,rivafb,nvidiafb,rivatv,amdgpu,radeon/g'", GRUB_FILES)
        bash_o("modprobe vfio && modprobe vfio-pci")

logger = log.get_logger(__name__)

def _get_memory(word):
    out = shell.call("grep '%s' /proc/meminfo" % word)
    (name, capacity) = out.split(':')
    capacity = re.sub('[k|K][b|B]', '', capacity).strip()
    #capacity = capacity.rstrip('kB').rstrip('KB').rstrip('kb').strip()
    return sizeunit.KiloByte.toByte(long(capacity))

def _get_total_memory():
    return _get_memory('MemTotal')

def _get_free_memory():
    return _get_memory('MemFree')

def _get_used_memory():
    return _get_total_memory() - _get_free_memory()

class HostPlugin(kvmagent.KvmAgent):
    '''
    classdocs
    '''

    CONNECT_PATH = '/host/connect'
    CAPACITY_PATH = '/host/capacity'
    ECHO_PATH = '/host/echo'
    FACT_PATH = '/host/fact'
    PING_PATH = "/host/ping"
    GET_USB_DEVICES_PATH = "/host/usbdevice/get"
    SETUP_MOUNTABLE_PRIMARY_STORAGE_HEARTBEAT = "/host/mountableprimarystorageheartbeat"
    UPDATE_OS_PATH = "/host/updateos"
    UPDATE_DEPENDENCY = "/host/updatedependency"
    ENABLE_HUGEPAGE = "/host/enable/hugepage"
    DISABLE_HUGEPAGE = "/host/disable/hugepage"
    CLEAN_LOCAL_CACHE = "/host/imagestore/cleancache"
    HOST_START_USB_REDIRECT_PATH = "/host/usbredirect/start"
    HOST_STOP_USB_REDIRECT_PATH = "/host/usbredirect/stop"
    CHECK_USB_REDIRECT_PORT = "/host/usbredirect/check"
    IDENTIFY_HOST = "/host/identify"
    GET_HOST_NETWORK_FACTS = "/host/networkfacts"
    HOST_XFS_SCRAPE_PATH = "/host/xfs/scrape"
    GET_PCI_DEVICES = "/pcidevice/get"
    CREATE_PCI_DEVICE_ROM_FILE = "/pcidevice/createrom"
    GENERATE_SRIOV_PCI_DEVICES = "/pcidevice/generate"
    UNGENERATE_SRIOV_PCI_DEVICES = "/pcidevice/ungenerate"
    GENERATE_VFIO_MDEV_DEVICES = "/mdevdevice/generate"
    UNGENERATE_VFIO_MDEV_DEVICES = "/mdevdevice/ungenerate"

    def _get_libvirt_version(self):
        ret = shell.call('libvirtd --version')
        return ret.split()[-1]

    def _get_qemu_version(self):
        # to be compatible with both `2.6.0` and `2.9.0(qemu-kvm-ev-2.9.0-16.el7_4.8.1)`
        ret = shell.call('%s -version' % kvmagent.get_qemu_path())
        words = ret.split()
        for w in words:
            if w == 'version':
                return words[words.index(w)+1].strip().split('(')[0]

        raise kvmagent.KvmError('cannot get qemu version[%s]' % ret)

    def _prepare_firewall_for_migration(self):
        """Prepare firewall rules for libvirt live migration."""

        mrule = "-A INPUT -p tcp -m tcp --dport 49152:49261 -j ACCEPT"
        rules = bash_o("iptables -w -S INPUT").splitlines()
        if not mrule in rules:
            bash_r("iptables -w %s" % mrule.replace("-A ", "-I "))

    @lock.file_lock('/run/xtables.lock')
    @in_bash
    def apply_iptables_rules(self, rules):
        logger.debug("starting add iptables rules : %s" % rules)
        if len(rules) != 0 and rules is not None:
            for item in rules:
                rule = item.strip("'").strip('"')
                clean_rule = ' '.join(rule.split(' ')[1:])
                ret = bash_r("iptables -C %s " % clean_rule)
                if ret == 0:
                    continue
                elif ret == 1:
                    # didn't find this rule
                    set_rules_ret = bash_r("iptables %s" % rule)
                    if set_rules_ret != 0:
                        raise Exception('cannot set iptables rule: %s' % rule)
                else:
                    raise Exception('check iptables rule: %s failed' % rule)
        return True

    @kvmagent.replyerror
    def connect(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = ConnectResponse()

        # page table extension
        if shell.run('lscpu | grep -q -w GenuineIntel') == 0:
            new_ept = False if cmd.pageTableExtensionDisabled else True
            rsp.error = self._set_intel_ept(new_ept)
            if rsp.error is not None:
                rsp.success = False
                return jsonobject.dumps(rsp)

        self.host_uuid = cmd.hostUuid
        self.config[kvmagent.HOST_UUID] = self.host_uuid
        self.config[kvmagent.SEND_COMMAND_URL] = cmd.sendCommandUrl
        Report.serverUuid = self.host_uuid
        Report.url = cmd.sendCommandUrl
        logger.debug(http.path_msg(self.CONNECT_PATH, 'host[uuid: %s] connected' % cmd.hostUuid))
        rsp.libvirtVersion = self.libvirt_version
        rsp.qemuVersion = self.qemu_version

        # create udev rule
        self.handle_usb_device_events()

        ignore_msrs = 1 if cmd.ignoreMsrs else 0
        shell.run("/bin/echo %s > /sys/module/kvm/parameters/ignore_msrs" % ignore_msrs)

        vm_plugin.cleanup_stale_vnc_iptable_chains()
        apply_iptables_result = self.apply_iptables_rules(cmd.iptablesRules)
        rsp.iptablesSucc = apply_iptables_result
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    def ping(self, req):
        rsp = PingResponse()
        rsp.hostUuid = self.host_uuid
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    def echo(self, req):
        logger.debug('get echoed')
        loop = 0
        while linux.fake_dead('kvmagent') is True and loop < 1200:
            logger.debug('checked fake dead, sleep 3 secs')
            time.sleep(3)
            loop += 1
        return ''

    @kvmagent.replyerror
    def fact(self, req):
        rsp = HostFactResponse()
        rsp.osDistribution, rsp.osVersion, rsp.osRelease = platform.dist()
        # to be compatible with both `2.6.0` and `2.9.0(qemu-kvm-ev-2.9.0-16.el7_4.8.1)`
        qemu_img_version = shell.call("qemu-img --version | grep 'qemu-img version' | cut -d ' ' -f 3 | cut -d '(' -f 1")
        qemu_img_version = qemu_img_version.strip('\t\r\n ,')
        ipV4Addrs = shell.call("ip addr | grep -w inet | grep -v 127.0.0.1 | awk '!/zs$/{print $2}' | cut -d/ -f1")
        rsp.systemProductName = 'unknown'
        rsp.systemSerialNumber = 'unknown'
        is_dmidecode = shell.run("dmidecode")
        if str(is_dmidecode) == '0':
            system_product_name = shell.call('dmidecode -s system-product-name').strip()
            baseboard_product_name = shell.call('dmidecode -s baseboard-product-name').strip()
            system_serial_number = shell.call('dmidecode -s system-serial-number').strip()
            rsp.systemSerialNumber = system_serial_number if system_serial_number else 'unknown'
            rsp.systemProductName = system_product_name if system_product_name else baseboard_product_name

        rsp.qemuImgVersion = qemu_img_version
        rsp.libvirtVersion = self.libvirt_version
        rsp.ipAddresses = ipV4Addrs.splitlines()
        if IS_AARCH64:
            # FIXME how to check vt of aarch64?
            rsp.hvmCpuFlag = 'vt'
            cpu_model = None
            try:
                cpu_model = self._get_host_cpu_model()
            except AttributeError:
                logger.debug("maybe XmlObject has no attribute model, use uname -p to get one")
                if cpu_model is None:
                    cpu_model = os.uname()[-1]

            rsp.cpuModelName = cpu_model
            rsp.hostCpuModelName = "aarch64"

            cpuMHz = shell.call("lscpu | awk '/max MHz/{ print $NF }'")
            # in case lscpu doesn't show cpu max mhz
            cpuMHz = "2500.0000" if cpuMHz.strip() == '' else cpuMHz
            rsp.cpuGHz = '%.2f' % (float(cpuMHz) / 1000)
        else:
            if shell.run('grep vmx /proc/cpuinfo') == 0:
                rsp.hvmCpuFlag = 'vmx'

            if not rsp.hvmCpuFlag:
                if shell.run('grep svm /proc/cpuinfo') == 0:
                    rsp.hvmCpuFlag = 'svm'

            rsp.cpuModelName = self._get_host_cpu_model()

            host_cpu_info = shell.call("grep -m2 -P -o '(model name|cpu MHz)\s*:\s*\K.*' /proc/cpuinfo").splitlines()
            host_cpu_model_name = host_cpu_info[0]
            rsp.hostCpuModelName = host_cpu_model_name

            transient_cpuGHz = '%.2f' % (float(host_cpu_info[1]) / 1000)
            static_cpuGHz_re = re.search('[0-9.]*GHz', host_cpu_model_name)
            rsp.cpuGHz = static_cpuGHz_re.group(0)[:-3] if static_cpuGHz_re else transient_cpuGHz

        return jsonobject.dumps(rsp)

    @vm_plugin.LibvirtAutoReconnect
    def _get_host_cpu_model(conn):
        xml_object = xmlobject.loads(conn.getCapabilities())
        return str(xml_object.host.cpu.model.text_)


    @kvmagent.replyerror
    @in_bash
    def capacity(self, req):
        rsp = HostCapacityResponse()
        rsp.cpuNum = linux.get_cpu_num()
        rsp.cpuSpeed = linux.get_cpu_speed()
        (used_cpu, used_memory) = vm_plugin.get_cpu_memory_used_by_running_vms()
        rsp.usedCpu = used_cpu
        rsp.totalMemory = _get_total_memory()
        rsp.usedMemory = used_memory

        sockets = bash_o('grep "physical id" /proc/cpuinfo | sort -u | wc -l').strip('\n')
        rsp.cpuSockets = int(sockets)
        if rsp.cpuSockets == 0:
            rsp.cpuSockets = 1

        ret = jsonobject.dumps(rsp)
        logger.debug('get host capacity: %s' % ret)
        return ret

    def _heartbeat_func(self, heartbeat_file):
        class Heartbeat(object):
            def __init__(self):
                self.current = None

        hb = Heartbeat()
        hb.current = time.time()
        with open(heartbeat_file, 'w') as fd:
            fd.write(jsonobject.dumps(hb))
        return True

    def _get_intel_ept(self):
        text = None
        with open('/sys/module/kvm_intel/parameters/ept', 'r') as reader:
            text = reader.read()
        return text is None or text.strip() == "Y"

    def _set_intel_ept(self, new_ept):
        error = None
        old_ept = self._get_intel_ept()
        if new_ept != old_ept:
            param = "ept=%d" % new_ept
            if shell.run("modprobe -r kvm-intel") != 0 or shell.run("modprobe kvm-intel %s" % param) != 0:
                error = "failed to reload kvm-intel, please stop the running VM on the host and try again."
            else:
                with open('/etc/modprobe.d/intel-ept.conf', 'w') as writer:
                    writer.write("options kvm_intel %s" % param)
                logger.info("_set_intel_ept(%s) OK." % new_ept)

        if error is not None:
            logger.warn("_set_intel_ept: %s" % error)
        return error

    @kvmagent.replyerror
    def setup_heartbeat_file(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = SetupMountablePrimaryStorageHeartbeatResponse()

        for hb in cmd.heartbeatFilePaths:
            hb_dir = os.path.dirname(hb)
            mount_path = os.path.dirname(hb_dir)
            if not linux.is_mounted(mount_path):
                rsp.error = '%s is not mounted, setup heartbeat file[%s] failed' % (mount_path, hb)
                rsp.success = False
                return jsonobject.dumps(rsp)

        for hb in cmd.heartbeatFilePaths:
            t = self.heartbeat_timer.get(hb, None)
            if t:
                t.cancel()

            hb_dir = os.path.dirname(hb)
            if not os.path.exists(hb_dir):
                os.makedirs(hb_dir, 0755)

            t = thread.timer(cmd.heartbeatInterval, self._heartbeat_func, args=[hb], stop_on_exception=False)
            t.start()
            self.heartbeat_timer[hb] = t
            logger.debug('create heartbeat file at[%s]' % hb)

        return jsonobject.dumps(rsp)

    def _get_next_available_port(self):
        for port in range(4100, 4200):
            if bash_r("netstat -nap | grep :%s[[:space:]] | grep LISTEN" % port) != 0:
                return port
        raise kvmagent.KvmError('no more available port for start usbredirect server')

    @kvmagent.replyerror
    @in_bash
    def start_usb_redirect_server(self, req):
        def _start_usb_server(port, busNum, devNum):
            iptc = iptables.from_iptables_save()
            iptc.add_rule('-A INPUT -p tcp -m tcp --dport %s -j ACCEPT' % port)
            iptc.iptable_restore()
            systemd_service_name = "usbredir-%s-%s-%s" % (port, busNum, devNum)
            if bash_r("systemctl list-units |grep %s" % systemd_service_name) == 0:
                bash_r("systemctl start %s" % systemd_service_name)
            else:
                ret, output = bash_ro("systemd-run --unit %s usbredirserver -p %s %s-%s" % (systemd_service_name, port, busNum, devNum))
                if ret != 0:
                    logger.info("usb %s-%s start failed on port %s" % (busNum, devNum, port))
                    return False, output
            logger.info("usb %s-%s start successed on port %s" % (busNum, devNum, port))
            return True, None

        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = StartUsbRedirectServerRsp()
        port = cmd.port if cmd.port is not None else self._get_next_available_port()
        ret, output = _start_usb_server(int(port), cmd.busNum, cmd.devNum)
        if ret:
            rsp.port = int(port)
            return jsonobject.dumps(rsp)
        else:
            rsp.success = False
            rsp.error = output
            return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def stop_usb_redirect_server(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = StopUsbRedirectServerRsp()
        if bash_r("netstat -nap | grep :%s[[:space:]] | grep LISTEN | grep usbredir" % cmd.port) != 0:
            logger.info("port %s is not occupied by usbredir" % cmd.port)
        bash_r("systemctl stop usbredir-%s-%s-%s" % (cmd.port, cmd.busNum, cmd.devNum))
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def check_usb_server_port(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = CheckUsbServerPortRsp()
        r, o, e = bash_roe("netstat -nap | grep LISTEN | grep usbredir  | awk '{print $4}' | awk -F ':' '{ print $4 }'")
        if r != 0:
            rsp.success = False
            rsp.error = "unable to get started usb server port"
            return jsonobject.dumps(rsp)
        existPort = o.split("\n")
        for value in cmd.portList:
            uuid = str(value).split(":")[0]
            port = str(value).split(":")[1]
            if port not in existPort:
                rsp.uuids.append(uuid)
                continue
            existPort.remove(port)
        # kill stale usb server
        for port in existPort:
            bash_r("systemctl stop usbredir-%s" % port)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def get_usb_devices(self, req):
        class UsbDeviceInfo(object):
            def __init__(self):
                self.busNum = ""
                self.devNum = ""
                self.idVendor = ""
                self.idProduct = ""
                self.iManufacturer = ""
                self.iProduct = ""
                self.iSerial = ""
                self.usbVersion = ""
            def toString(self):
                return self.busNum + ':' + self.devNum + ':' + self.idVendor + ':' + self.idProduct + ':' + self.iManufacturer + ':' + self.iProduct + ':' + self.iSerial + ':' + self.usbVersion + ";"

        # use 'lsusb.py -U' to get device ID, like '0751:9842'
        rsp = GetUsbDevicesRsp()
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        r, o, e = bash_roe("lsusb.py -U")
        if r != 0:
            rsp.success = False
            rsp.error = "%s %s" % (e, o)
            return jsonobject.dumps(rsp)

        idSet = set()
        usbDevicesInfo = ''
        for line in o.split('\n'):
            line = line.split()
            if len(line) < 2:
                continue
            idSet.add(line[1])

        for devId in idSet:
            # use 'lsusb -v -d ID' to get device info[s]
            r, o, e = bash_roe("lsusb -v -d %s" % devId)
            if r != 0:
                rsp.success = False
                rsp.error = "%s %s" % (e, o)
                return jsonobject.dumps(rsp)

            for line in o.split('\n'):
                line = line.strip().split()
                if len(line) < 2:
                    continue

                if line[0] == 'Bus':
                    info = UsbDeviceInfo()
                    info.idVendor, info.idProduct = devId.split(':')
                    info.busNum = line[1]
                    info.devNum = line[3].rsplit(':')[0]
                elif line[0] == 'idVendor':
                    info.iManufacturer = ' '.join(line[2:]) if len(line) > 2 else ""
                elif line[0] == 'idProduct':
                    info.iProduct = ' '.join(line[2:]) if len(line) > 2 else ""
                elif line[0] == 'bcdUSB':
                    info.usbVersion = line[1]
                    # special case: USB2.0 with speed 1.5MBit/s or 12MBit/s should be attached to USB1.1 Controller
                    rst = bash_r("lsusb.py | grep -v 'grep' | grep '%s' | grep -E '1.5MBit/s|12MBit/s'" % devId)
                    info.usbVersion = info.usbVersion if rst != 0 else '1.1'
                elif line[0] == 'iManufacturer' and len(line) > 2:
                    info.iManufacturer = ' '.join(line[2:])
                elif line[0] == 'iProduct' and len(line) > 2:
                    info.iProduct = ' '.join(line[2:])
                elif line[0] == 'iSerial':
                    info.iSerial = ' '.join(line[2:]) if len(line) > 2 else ""
                    if info.busNum == '' or info.devNum == '' or info.idVendor == '' or info.idProduct == '':
                        rsp.success = False
                        rsp.error = "cannot get enough info of usb device"
                        return jsonobject.dumps(rsp)
                    else:
                        usbDevicesInfo += info.toString()
        rsp.usbDevicesInfo = usbDevicesInfo
        return jsonobject.dumps(rsp)

    @lock.file_lock('/usr/bin/_report_device_event.sh')
    def handle_usb_device_events(self):
        bash_str = """#!/usr/bin/env python
import urllib2
def post_msg(data, post_url):
    headers = {"content-type": "application/json", "commandpath": "/host/reportdeviceevent"}
    req = urllib2.Request(post_url, data, headers)
    response = urllib2.urlopen(req)
    response.close()

if __name__ == "__main__":
    post_msg("{'hostUuid':'%s'}", '%s')
""" % (self.config.get(kvmagent.HOST_UUID), self.config.get(kvmagent.SEND_COMMAND_URL))

        bash_file = '/usr/bin/_report_device_event.py'
        with open(bash_file, 'w') as f:
            f.write(bash_str)
        os.chmod(bash_file, 0o755)

        rule_str = 'ACTION=="add|remove", SUBSYSTEM=="usb", RUN="%s"' % bash_file
        rule_path = '/etc/udev/rules.d/'
        rule_file = os.path.join(rule_path, 'usb.rules')
        if not os.path.exists(rule_path):
            os.makedirs(rule_path)
        with open(rule_file, 'w') as f:
            f.write(rule_str)

    @kvmagent.replyerror
    @in_bash
    def update_os(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        if not cmd.excludePackages:
            exclude = ""
        else:
            exclude = "--exclude=" + cmd.excludePackages
        yum_cmd = "yum --enablerepo=* clean all && yum --disablerepo=* --enablerepo=zstack-mn,qemu-kvm-ev-mn %s update -y" % exclude

        rsp = UpdateHostOSRsp()
        if shell.run("which yum") != 0:
            rsp.success = False
            rsp.error = "no yum command found, cannot update host os"
        elif shell.run("yum --disablerepo=* --enablerepo=zstack-mn repoinfo") != 0:
            rsp.success = False
            rsp.error = "no zstack-mn repo found, cannot update host os"
        elif shell.run("yum --disablerepo=* --enablerepo=qemu-kvm-ev-mn repoinfo") != 0:
            rsp.success = False
            rsp.error = "no qemu-kvm-ev-mn repo found, cannot update host os"
        elif shell.run(yum_cmd) != 0:
            rsp.success = False
            rsp.error = "failed to update host os using zstack-mn,qemu-kvm-ev-mn repo"
        else:
            logger.debug("successfully run: %s" % yum_cmd)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def update_dependency(self, req):
        rsp = UpdateDependencyRsp()
        yum_cmd = "yum --enablerepo=* clean all && yum --disablerepo=* --enablerepo=zstack-mn,qemu-kvm-ev-mn install `cat /var/lib/zstack/dependencies` -y"
        if shell.run("which yum") != 0:
            rsp.success = False
            rsp.error = "no yum command found, cannot update kvmagent dependencies"
        elif shell.run("yum --disablerepo=* --enablerepo=zstack-mn repoinfo") != 0:
            rsp.success = False
            rsp.error = "no zstack-mn repo found, cannot update kvmagent dependencies"
        elif shell.run("yum --disablerepo=* --enablerepo=qemu-kvm-ev-mn repoinfo") != 0:
            rsp.success = False
            rsp.error = "no qemu-kvm-ev-mn repo found, cannot update kvmagent dependencies"
        elif shell.run(yum_cmd) != 0:
            rsp.success = False
            rsp.error = "failed to update kvmagent dependencies using zstack-mn,qemu-kvm-ev-mn repo"
        else:
            logger.debug("successfully run: %s" % yum_cmd)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def get_xfs_frag_data(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = GetXfsFragDataRsp()
        o = bash_o("df -hlT | awk 'NR==2 {print $1,$2}'")
        o = str(o).strip()
        root_path = o.split(" ")[0]
        fs_type = o.split(" ")[1]
        rsp.fsType = fs_type
        if fs_type != "xfs":
            return jsonobject.dumps(rsp)
        if root_path is None:
            logger.warn("failed to find root device")
        else:
            frag_percent = bash_o("xfs_db -c frag -r /dev/mapper/zstack-root | awk '/fragmentation factor/{print $7}'", True)

        if not str(frag_percent).strip().endswith("%"):
            logger.info("error format %s" % frag_percent)
        else:
            rsp.hostFrag = frag_percent.strip()[:-1]
        volume_path_dict = cmd.volumePathMap.__dict__
        if volume_path_dict is not None:
            for key, value in volume_path_dict.items():
                r, o = bash_ro("xfs_bmap %s | wc -l" % value, True)
                if r == 0:
                    o = o.strip()
                    rsp.volumeFragMap[key] = int(o) - 1

        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def disable_hugepage(self, req):
        rsp = DisableHugePageRsp()
        return_code, stdout = self._close_hugepage()
        if return_code != 0 or "Error" in stdout:
            rsp.success = False
            rsp.error = stdout
        return jsonobject.dumps(rsp)

    def _close_hugepage(self):
        disable_hugepage_script = '''#!/bin/sh
grubs=("/boot/grub2/grub.cfg" "/boot/grub/grub.cfg" "/etc/grub2-efi.cfg" "/etc/grub-efi.cfg")        

# config nr_hugepages
sysctl -w vm.nr_hugepages=0

# enable nr_hugepages
sysctl vm.nr_hugepages=0

# config default grub
sed -i '/GRUB_CMDLINE_LINUX=/s/[[:blank:]]*hugepagesz[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' /etc/default/grub
sed -i '/GRUB_CMDLINE_LINUX=/s/[[:blank:]]*hugepages[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' /etc/default/grub
sed -i '/GRUB_CMDLINE_LINUX=/s/[[:blank:]]*transparent_hugepage[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' /etc/default/grub
line=`cat /etc/default/grub | grep GRUB_CMDLINE_LINUX`
result=$(echo $line | grep '\"$') 
if [ ! -n "$result" ]; then 
    sed -i '/GRUB_CMDLINE_LINUX/s/$/\"/g' /etc/default/grub
fi

#clean boot grub config
for var in ${grubs[@]} 
do 
   if [ -f $var ]; then
       sed -i '/^[[:space:]]*linux/s/[[:blank:]]*hugepagesz[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' $var
       sed -i '/^[[:space:]]*linux/s/[[:blank:]]*hugepages[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' $var
       sed -i '/^[[:space:]]*linux/s/[[:blank:]]*transparent_hugepage[[:blank:]]*=[[:blank:]]*[[:graph:]]*//g' $var
   fi    
done
'''
        fd, disable_hugepage_script_path = tempfile.mkstemp()
        with open(disable_hugepage_script_path, 'w') as f:
            f.write(disable_hugepage_script)
        logger.info('close_hugepage_script_path is: %s' % disable_hugepage_script_path)
        cmd = shell.ShellCmd('bash %s' % disable_hugepage_script_path)
        cmd(False)

        os.remove(disable_hugepage_script_path)
        return cmd.return_code, cmd.stdout

    @kvmagent.replyerror
    @in_bash
    def enable_hugepage(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = EnableHugePageRsp()

        # clean old hugepage config
        return_code, stdout = self._close_hugepage()
        if return_code != 0 or "Error" in stdout:
            rsp.success = False
            rsp.error = stdout
            return jsonobject.dumps(rsp)

        pageSize = cmd.pageSize
        reserveSize = cmd.reserveSize
        enable_hugepage_script = '''#!/bin/sh
grubs=("/boot/grub2/grub.cfg" "/boot/grub/grub.cfg" "/etc/grub2-efi.cfg" "/etc/grub-efi.cfg")          

# byte to mib
let "reserveSize=%s/1024/1024"
pageSize=%s
memSize=`free -m | awk '/:/ {print $2;exit}'`
let "pageNum=(memSize-reserveSize)/pageSize"
if [ $memSize -lt $reserveSize ]                                                                                                                                                                                   
then
    echo "Error:reserve size is bigger than system memory size"
    exit 1
fi
#drop cache 
echo 3 > /proc/sys/vm/drop_caches

# enable Transparent HugePages
echo always > /sys/kernel/mm/transparent_hugepage/enabled

# config grub
sed -i '/GRUB_CMDLINE_LINUX=/s/\"$/ transparent_hugepage=always hugepagesz=\'\"$pageSize\"\'M hugepages=\'\"$pageNum\"\'\"/g' /etc/default/grub

#config boot grub
for var in ${grubs[@]} 
do 
   if [ -f $var ]; then
       sed -i '/^[[:space:]]*linux/s/$/ transparent_hugepage=always hugepagesz=\'\"$pageSize\"\'M hugepages=\'\"$pageNum\"\'/g' $var
   fi    
done
''' % (reserveSize, pageSize)

        fd, enable_hugepage_script_path = tempfile.mkstemp()
        with open(enable_hugepage_script_path, 'w') as f:
            f.write(enable_hugepage_script)
        logger.info('enable_hugepage_script_path is: %s' % enable_hugepage_script_path)
        cmd = shell.ShellCmd('bash %s' % enable_hugepage_script_path)
        cmd(False)
        if cmd.return_code != 0 or "Error" in cmd.stdout:
            rsp.success = False
            rsp.error = cmd.stdout
        os.remove(enable_hugepage_script_path)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    def clean_local_cache(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        isc = ImageStoreClient()
        isc.clean_imagestore_cache(cmd.mountPath)
        return jsonobject.dumps(kvmagent.AgentResponse())

    def identify_host(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = kvmagent.AgentResponse()
        sc = shell.ShellCmd("ipmitool chassis identify %s" % cmd.interval)
        sc(True)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    def get_host_network_facts(self, req):
        rsp = GetHostNetworkBongdingResponse()
        rsp.bondings = self.get_host_networking_bonds()
        rsp.nics = self.get_host_networking_interfaces()
        return jsonobject.dumps(rsp)

    @staticmethod
    def get_host_networking_interfaces():
        nics = []
        nic_names = bash_o("find /sys/class/net -type l -not -lname '*virtual*' -printf '%f\\n'").splitlines()
        if len(nic_names) == 0:
            return nics
        for nic in nic_names:
            nics.append(HostNetworkInterfaceInventory(nic.strip()))
        return nics

    @staticmethod
    def get_host_networking_bonds():
        bonds = []
        r, bond_names = bash_ro("cat /sys/class/net/bonding_masters")
        if r != 0:
            return bonds
        bond_names = bond_names.strip().split(" ")
        if len(bond_names) == 0:
            return bonds
        for bond in bond_names:
            bonds.append(HostNetworkBondingInventory(bond))
        return bonds

    def _get_sriov_info(self, to):
        addr = to.pciDeviceAddress
        if not addr.startswith('0000:'):
            addr = "0000:" + addr
        dev = os.path.join("/sys/bus/pci/devices/", addr)
        totalvfs = os.path.join(dev, "sriov_totalvfs")
        physfn = os.path.join(dev, "physfn")
        gpuvf  = os.path.join(dev, "gpuvf")

        if os.path.exists(totalvfs):
            # for pf, to.maxPartNum means the number of possible vfs
            with open(totalvfs, 'r') as f:
                to.maxPartNum = f.read()
            if os.path.exists(gpuvf):
                to.virtStatus = "SRIOV_VIRTUALIZED"
            else:
                to.virtStatus = "SRIOV_VIRTUALIZABLE"
        elif os.path.exists(physfn):
            # for vf, to.maxPartNum means the number of current vfs
            numvfs = os.path.join(physfn, "sriov_numvfs")
            if os.path.exists(numvfs):
                with open(numvfs, 'r') as f:
                    to.maxPartNum = f.read()
            to.virtStatus = "SRIOV_VIRTUAL"
            # ../0000:06:00.0 --> 06:00.0
            to.parentAddress = os.readlink(physfn).split('0000:')[-1]
            if os.path.exists(gpuvf):
                with open(gpuvf, 'r') as f:
                    for line in f.readlines():
                        line = line.strip()
                        if 'VF FB Size' in line:
                            to.ramSize = line.split(':')[-1].strip()
                            to.description = "%s [RAM Size: %s]" % (to.description, to.ramSize)
                            break
        else:
            return False
        return True

    def _get_vfio_mdev_info(self, to):
        addr = to.pciDeviceAddress
        if not addr.startswith("0000:"):
            addr = "0000:" + addr

        r, o, e = bash_roe("nvidia-smi vgpu -i %s -v -s" % addr)
        if r != 0:
            return False  # only support nvidia-smi now

        r, o, e = bash_roe("nvidia-smi vgpu -i %s -v -s | grep -v %s" % (addr, addr))
        for line in o.split('\n'):
            parts = line.split(':')
            if len(parts) < 2: continue
            title = parts[0].strip()
            content = ' '.join(parts[1:]).strip()
            if title == "vGPU Type ID":
                spec = {'TypeId': content}
                to.mdevSpecifications.append(spec)
            else:
                to.mdevSpecifications[-1][title] = content

        # if supported specs != creatable specs, means it's aleady virtualized
        _, support, _ = bash_roe("nvidia-smi vgpu -i %s -s | grep -v %s" % (addr, addr))
        _, creatable, _ = bash_roe("nvidia-smi vgpu -i %s -c | grep -v %s" % (addr, addr))
        if support != creatable:
            to.virtStatus = "VFIO_MDEV_VIRTUALIZED"
        else:
            to.virtStatus = "VFIO_MDEV_VIRTUALIZABLE"
        return True

    def _simplify_pci_device_name(self, name):
        if 'Intel Corporation' in name:
            return 'Intel'
        elif 'Advanced Micro Devices' in name:
            return 'AMD'
        elif 'NVIDIA Corporation' in name:
            return 'NVIDIA'
        else:
            return name

    @in_bash
    def _collect_format_pci_device_info(self, rsp):
        r, o, e = bash_roe("lspci -mmnnv")
        if r != 0:
            rsp.success = False
            rsp.error = "%s, %s" % (e, o)
            return

        # parse lspci output
        for part in o.split('\n\n'):
            vendor_name = ""
            device_name = ""
            subvendor_name = ""
            to = PciDeviceTO()
            for line in part.split('\n'):
                if len(line.split(':')) < 2: continue
                title = line.split(':')[0].strip()
                content = line.split(':')[1].strip()
                if title == 'Slot':
                    content = line[5:].strip()
                    to.pciDeviceAddress = content
                    r, o, e = bash_roe("lspci -s %s" % content)
                    if r == 0:
                        descs = ' '.join(o.split(' ')[1:]).strip().split(':')
                        to.description = ':'.join(descs[1:]) + ', ' + descs[0]
                elif title == 'Class':
                    _class = content.strip('[')
                    gpu_vendors = ["NVIDIA", "AMD"]
                    if any(vendor in to.description for vendor in gpu_vendors) \
                            and 'VGA compatible controller' in _class:
                        to.type = "GPU_Video_Controller"
                    elif any(vendor in to.description for vendor in gpu_vendors) \
                            and 'Audio device' in _class:
                        to.type = "GPU_Audio_Controller"
                    elif any(vendor in to.description for vendor in gpu_vendors) \
                            and '3D controller' in _class:
                        to.type = "GPU_3D_Controller"
                    elif 'Ethernet controller' in _class:
                        to.type = "Ethernet_Controller"
                    elif 'Moxa Technologies' in _class:
                        to.type = "Moxa_Device"
                    else:
                        to.type = "Generic"
                elif title == 'Vendor':
                    vendor_name = self._simplify_pci_device_name('['.join(content.split('[')[:-1]).strip())
                    to.vendorId = content.split('[')[-1].strip(']')
                elif title == "Device":
                    device_name = self._simplify_pci_device_name('['.join(content.split('[')[:-1]).strip())
                    to.deviceId = content.split('[')[-1].strip(']')
                elif title == "SVendor":
                    subvendor_name = self._simplify_pci_device_name('['.join(content.split('[')[:-1]).strip())
                    to.subvendorId = content.split('[')[-1].strip(']')
                elif title == "SDevice":
                    to.subdeviceId = content.split('[')[-1].strip(']')
            to.name = "%s_%s" % (subvendor_name if subvendor_name else vendor_name, device_name)
            if not self._get_sriov_info(to) and not self._get_vfio_mdev_info(to):
                to.virtStatus = "UNVIRTUALIZABLE"
            if to.vendorId != '' and to.deviceId != '':
                rsp.pciDevicesInfo.append(to)

    # moved from vm_plugin to host_plugin
    @kvmagent.replyerror
    @in_bash
    def get_pci_info(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = GetPciDevicesResponse()

        # update grub to enable/disable iommu in host
        updateConfigration = UpdateConfigration()
        updateConfigration.path = "/etc/default/grub"
        updateConfigration.enableIommu = cmd.enableIommu
        success, error = updateConfigration.updateHostIommu()
        if success is False:
            rsp.success = False
            rsp.error = error
            return jsonobject.dumps(rsp)

        updateConfigration.updateGrubConfig()

        r_bios, o_bios, e_bios = bash_roe("find /sys -iname dmar*")
        r_kernel, o_kernel, e_kernel = bash_roe("grep 'intel_iommu=on' /proc/cmdline")
        if o_bios != '' and r_kernel == 0:
            rsp.hostIommuStatus = True
        else:
            rsp.hostIommuStatus = False

        # get pci device info
        self._collect_format_pci_device_info(rsp)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    def create_pci_device_rom_file(self, req):
        PCI_ROM_PATH = "/var/lib/zstack/pcirom"
        if not os.path.exists(PCI_ROM_PATH):
            os.mkdir(PCI_ROM_PATH)

        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = CreatePciDeviceRomFileRsp()
        rom_file = os.path.join(PCI_ROM_PATH, cmd.specUuid)
        if not cmd.romContent and os.path.exists(rom_file):
            logger.debug("delete rom file %s because no content in db anymore" % rom_file)
            os.remove(rom_file)
        elif cmd.romMd5sum != hashlib.md5(cmd.romContent).hexdigest():
            rsp.success = False
            rsp.error = "md5sum of pci rom file[uuid:%s] does not match" % cmd.specUuid
            return jsonobject.dumps(rsp)
        else:
            content = base64.b64decode(cmd.romContent)
            with open(rom_file, 'wb') as f:
                f.write(content)
            logger.debug("successfully write rom content into %s" % rom_file)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def generate_sriov_pci_devices(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = GenerateSriovPciDevicesRsp()
        logger.debug("generate_sriov_pci_devices: pciAddr[%s], reSplite[%s]" % (cmd.pciDeviceAddress, cmd.reSplite))

        GIM = "gim"         # gpu-iov kernel module, for AMD MxGPU only

        # get device type using device address, get device driver using device type
        addr = cmd.pciDeviceAddress
        if not addr.startswith('0000:'):
            addr = "0000:" + addr
        dev = os.path.join("/sys/bus/pci/devices/", addr)
        totalvfs = os.path.join(dev, "sriov_totalvfs")
        if os.path.exists(totalvfs):
            ko_name = GIM
        else:
            rsp.success = False
            rsp.error = "do not support sriov of pci device [addr:%s]" % addr
            return jsonobject.dumps(rsp)

        if ko_name == GIM:
            # ramdisk file in /dev/shm to mark host rebooting
            ramdisk = "/dev/shm/pci_sriov_gim"
            if cmd.reSplite and os.path.exists(ramdisk):
                logger.debug("no need to re-splite pci device[addr:%s] into sriov pci devices" % addr)
                return jsonobject.dumps(rsp)

            # make install mxgpu driver if need to
            mxgpu_driver_tar = "/var/lib/zstack/mxgpu_driver.tar.gz"
            if os.path.exists(mxgpu_driver_tar):
                r, o, e = bash_roe("tar xvf %s -C /tmp; cd /tmp/mxgpu_driver; make install" % mxgpu_driver_tar)
                if r != 0:
                    rsp.success = False
                    rsp.error = "failed to install mxgpu driver, %s, %s" % (o, e)
                    return jsonobject.dumps(rsp)
                # rm mxgpu driver tar
                os.remove(mxgpu_driver_tar)

            # check installed ko
            r, _, _ = bash_roe("lsmod | grep gim")
            if r == 0:
                rsp.success = False
                rsp.error = "gim.ko already installed, need to run `modprobe -r gim` first"
                return jsonobject.dumps(rsp)

            # prepare gim_config
            gim_config = "/etc/gim_config"
            with open(gim_config, 'w') as f:
                f.write("vf_num=%s" % cmd.virtPartNum)

            # install gim.ko
            r, o, e = bash_roe("modprobe gim")
            if r != 0:
                rsp.success = False
                rsp.error = "failed to install gim.ko, %s, %s" % (o, e)
                return jsonobject.dumps(rsp)

        # create ramdisk file after pci device virtualization
        open(ramdisk, 'a').close()
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def ungenerate_sriov_pci_devices(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = UngenerateSriovPciDevicesRsp()

        GIM = "gim"         # gpu-iov kernel module, for AMD MxGPU only

        # get device type using device address, get device driver using device type
        addr = cmd.pciDeviceAddress
        if not addr.startswith('0000:'):
            addr = "0000:" + addr
        dev = os.path.join("/sys/bus/pci/devices/", addr)
        totalvfs = os.path.join(dev, "sriov_totalvfs")
        if os.path.exists(totalvfs):
            ko_name = GIM
        else:
            rsp.success = False
            rsp.error = "do not support sriov of pci device [addr:%s]" % addr
            return jsonobject.dumps(rsp)

        if ko_name == GIM:
            # remote gim.ko
            r, o, e = bash_roe("modprobe -r gim")
            if r != 0:
                rsp.success = False
                rsp.error = "failed to remove gim.ko, %s, %s" % (o, e)
                return jsonobject.dumps(rsp)
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def generate_vfio_mdev_devices(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = GenerateVfioMdevDevicesRsp()
        logger.debug("generate_vfio_mdev_devices: mdevUuids[%s]" % cmd.mdevUuids)

        # ramdisk file in /dev/shm to mark host rebooting
        addr = cmd.pciDeviceAddress
        ramdisk = os.path.join('/dev/shm', 'pci-' + addr)
        if cmd.mdevUuids and len(cmd.mdevUuids) != 0 and os.path.exists(ramdisk):
            logger.debug("no need to re-splite pci device[addr:%s] into mdev devices" % addr)
            return jsonobject.dumps(rsp)

        # support nvidia gpu only
        type = int(cmd.mdevSpecTypeId, 0)
        if not addr.startswith('0000:'):
            addr = "0000:" + addr
        spec_path = os.path.join("/sys/bus/pci/devices/", addr, "mdev_supported_types", "nvidia-%d" % type)
        if not os.path.exists(spec_path):
            rsp.success = False
            rsp.error = "cannot generate vfio mdev devices from pci device[addr:%s]" % addr
            return jsonobject.dumps(rsp)

        if cmd.mdevUuids and len(cmd.mdevUuids) != 0:
            for _uuid in cmd.mdevUuids:
                with open(os.path.join(spec_path, "create"), 'w') as f:
                    f.write(str(uuid.UUID(_uuid)))
                    logger.debug("re-generate mdev device[uuid:%s] from pci device[addr:%s]" % (_uuid, addr))
        else:
            with open(os.path.join(spec_path, "available_instances"), 'r') as f:
                max_instances = f.read().strip()
            for i in range(int(max_instances)):
                _uuid = str(uuid.uuid4())
                rsp.mdevUuids.append(_uuid)
                with open(os.path.join(spec_path, "create"), 'w') as f:
                    f.write(_uuid)
                    logger.debug("generate mdev device[uuid:%s] from pci device[addr:%s]" % (_uuid, addr))

        # create ramdisk file after pci device virtualization
        open(ramdisk, 'a').close()
        return jsonobject.dumps(rsp)

    @kvmagent.replyerror
    @in_bash
    def ungenerate_vfio_mdev_devices(self, req):
        cmd = jsonobject.loads(req[http.REQUEST_BODY])
        rsp = UngenerateVfioMdevDevicesRsp()

        # support nvidia gpu only
        addr = cmd.pciDeviceAddress
        type = int(cmd.mdevSpecTypeId, 0)
        if not addr.startswith('0000:'):
            addr = "0000:" + addr
        device_path = os.path.join("/sys/bus/pci/devices/", addr, "mdev_supported_types", "nvidia-%d" % type, "devices")
        if not os.path.exists(device_path):
            rsp.success = False
            rsp.error = "no vfio mdev devices to ungenerate from pci device[addr:%s]" % addr
            return jsonobject.dumps(rsp)

        # remove
        for _uuid in os.listdir(device_path):
            with open(os.path.join(device_path, _uuid, "remove"), 'w') as f:
                f.write("1")

        # check
        _, support, _ = bash_roe("nvidia-smi vgpu -i %s -s | grep -v %s" % (addr, addr))
        _, creatable, _ = bash_roe("nvidia-smi vgpu -i %s -c | grep -v %s" % (addr, addr))
        if support != creatable:
            rsp.success = False
            rsp.error = "failed to ungenerate vfio mdev devices from pci device[addr:%s]" % addr
        return jsonobject.dumps(rsp)

    def start(self):
        self.host_uuid = None

        http_server = kvmagent.get_http_server()
        http_server.register_sync_uri(self.CONNECT_PATH, self.connect)
        http_server.register_async_uri(self.PING_PATH, self.ping)
        http_server.register_async_uri(self.CAPACITY_PATH, self.capacity)
        http_server.register_sync_uri(self.ECHO_PATH, self.echo)
        http_server.register_async_uri(self.SETUP_MOUNTABLE_PRIMARY_STORAGE_HEARTBEAT, self.setup_heartbeat_file)
        http_server.register_async_uri(self.FACT_PATH, self.fact)
        http_server.register_async_uri(self.GET_USB_DEVICES_PATH, self.get_usb_devices)
        http_server.register_async_uri(self.UPDATE_OS_PATH, self.update_os)
        http_server.register_async_uri(self.UPDATE_DEPENDENCY, self.update_dependency)
        http_server.register_async_uri(self.ENABLE_HUGEPAGE, self.enable_hugepage)
        http_server.register_async_uri(self.DISABLE_HUGEPAGE, self.disable_hugepage)
        http_server.register_async_uri(self.CLEAN_LOCAL_CACHE, self.clean_local_cache)
        http_server.register_async_uri(self.HOST_START_USB_REDIRECT_PATH, self.start_usb_redirect_server)
        http_server.register_async_uri(self.HOST_STOP_USB_REDIRECT_PATH, self.stop_usb_redirect_server)
        http_server.register_async_uri(self.CHECK_USB_REDIRECT_PORT, self.check_usb_server_port)
        http_server.register_async_uri(self.IDENTIFY_HOST, self.identify_host)
        http_server.register_async_uri(self.GET_HOST_NETWORK_FACTS, self.get_host_network_facts)
        http_server.register_async_uri(self.HOST_XFS_SCRAPE_PATH, self.get_xfs_frag_data)
        http_server.register_async_uri(self.GET_PCI_DEVICES, self.get_pci_info)
        http_server.register_async_uri(self.CREATE_PCI_DEVICE_ROM_FILE, self.create_pci_device_rom_file)
        http_server.register_async_uri(self.GENERATE_SRIOV_PCI_DEVICES, self.generate_sriov_pci_devices)
        http_server.register_async_uri(self.UNGENERATE_SRIOV_PCI_DEVICES, self.ungenerate_sriov_pci_devices)
        http_server.register_async_uri(self.GENERATE_VFIO_MDEV_DEVICES, self.generate_vfio_mdev_devices)
        http_server.register_async_uri(self.UNGENERATE_VFIO_MDEV_DEVICES, self.ungenerate_vfio_mdev_devices)

        self.heartbeat_timer = {}
        self.libvirt_version = self._get_libvirt_version()
        self.qemu_version = self._get_qemu_version()
        self._prepare_firewall_for_migration()
        filepath = r'/etc/libvirt/qemu/networks/autostart/default.xml'
        if os.path.exists(filepath):
            os.unlink(filepath)

    def stop(self):
        pass

    def configure(self, config):
        self.config = config
