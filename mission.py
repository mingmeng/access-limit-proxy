# Copyright (C) 2011 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_3_parser
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import arp
from ryu.lib.packet import ipv4
from ryu.lib.packet import tcp
from ryu.ofproto import inet
from ryu.ofproto import ether

from ryu.lib import hub

detect_ip = '10.0.0.99'
detect_mac = '00:00:00:00:00:99'
host_proxy = '10.0.0.1'
web_server = '10.0.0.3'
web_proxy = '10.0.0.4'


class ProxyAccess(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ProxyAccess, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.ip_to_mac = {}
        self.dps = []
        self.limit={}       # 加上流表限制属性 用于存储被ban机器
        self.web_server_dp = None
        hub.spawn(self._redirect)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        print("successfully connect switch(dpid:%d)" % (datapath.id))
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.dps.append(datapath)
        # 将表0的流表项加入表1
        match = parser.OFPMatch()
        inst = [parser.OFPInstructionGotoTable(table_id=1)]
        self.add_flow(datapath, 0, match, table_id=0, inst=inst)
        # 给表1加上流表默认项
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions, table_id=1)

    def add_flow(self, datapath, priority, match, actions=[], table_id=0,
                 idle_timeout=0, hard_timeout=0, buffer_id=None, inst=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        if not inst:
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                 actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, table_id=table_id, idle_timeout=idle_timeout, hard_timeout=hard_timeout,
                                    buffer_id=buffer_id, priority=priority, match=match, instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority, table_id=table_id, idle_timeout=idle_timeout,
                                    hard_timeout=hard_timeout, match=match, instructions=inst)
        datapath.send_msg(mod)

    # 加入重定向流表项
    def _redirect(self):
        hub.sleep(3)
        self.ip_to_mac.setdefault(host_proxy)
        self.ip_to_mac.setdefault(web_server)
        self.ip_to_mac.setdefault(web_proxy)

        # 检测web服务器的mac地址和代理服务器的地址
        flag = False
        while not flag:
            flag = True
            for ip_addr in self.ip_to_mac.keys():
                if not self.ip_to_mac[ip_addr]:
                    flag = False
            if flag:
                break
            for ip_addr in self.ip_to_mac.keys():
                if not self.ip_to_mac[ip_addr]:
                    self.detect_mac(ip_addr)
            hub.sleep(3)

        ofproto = ofproto_v1_3
        parser = ofproto_v1_3_parser
        # 把非代理用户限制访问的流表项加入
        match = parser.OFPMatch(
            eth_type=ether.ETH_TYPE_IP, ipv4_dst=web_proxy,
            ip_proto=inet.IPPROTO_TCP, tcp_dst=80)
        actions = []
        self.add_flow(
            self.web_server_dp, 5, match, actions=actions, table_id=0)
        print("install flow-entry: prevent host derectly visit web_proxy(%s) \
        on switch %s" % (web_proxy, self.web_server_dp.id))

        hub.sleep(20)
        # 将来自代理用户的转发重定向
        match = parser.OFPMatch(
            eth_type=ether.ETH_TYPE_IP, ipv4_src=host_proxy, ipv4_dst=web_server,
            ip_proto=inet.IPPROTO_TCP, tcp_dst=80)
        set_proxy_mac_dst = parser.OFPActionSetField(
            eth_dst=self.ip_to_mac[web_proxy])
        set_proxy_ip_dst = parser.OFPActionSetField(ipv4_dst=web_proxy)
        actions = [set_proxy_mac_dst, set_proxy_ip_dst]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions),
                parser.OFPInstructionGotoTable(table_id=1)]
        self.add_flow(self.web_server_dp, 10, match, table_id=0, inst=inst)

        match = parser.OFPMatch(
            eth_type=ether.ETH_TYPE_IP, ipv4_src=web_proxy, ipv4_dst=host_proxy,
            ip_proto=inet.IPPROTO_TCP, tcp_src=80)
        set_server_mac_src = parser.OFPActionSetField(
            eth_src=self.ip_to_mac[web_server])
        set_server_ip_src = parser.OFPActionSetField(ipv4_src=web_server)
        actions = [set_server_mac_src, set_server_ip_src]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions),
                parser.OFPInstructionGotoTable(table_id=1)]
        self.add_flow(self.web_server_dp, 10, match, table_id=0, inst=inst)

    
    def detect_mac(self, ip_addr):
        pkt = packet.Packet()
        eth_r = ethernet.ethernet(ethertype=ether.ETH_TYPE_ARP,
                                  dst='ff:ff:ff:ff:ff:ff',
                                  src=detect_mac)
        arp_r = arp.arp(opcode=1, src_mac=detect_mac, src_ip=detect_ip,
                        dst_mac='00:00:00:00:00:00', dst_ip=ip_addr)
        pkt.add_protocol(eth_r)
        pkt.add_protocol(arp_r)
        pkt.serialize()
        for datapath in self.dps:
            actions = [
                datapath.ofproto_parser.OFPActionOutput(port=datapath.ofproto.OFPP_FLOOD)]
            arp_request = datapath.ofproto_parser.OFPPacketOut(datapath=datapath,
                                                               in_port=datapath.ofproto.OFPP_CONTROLLER,
                                                               buffer_id=datapath.ofproto.OFP_NO_BUFFER,
                                                               actions=actions,
                                                               data=pkt.data
                                                               )
            datapath.send_msg(arp_request)
        print("send detect_arp request:i am %s,who is %s" % (detect_ip, ip_addr))

    def arp_reply_handler(self, msg, datapath, eth, arp):
        if (arp.opcode == 2) and (eth.dst == detect_mac):
            for ip_addr in self.ip_to_mac.keys():
                if arp.src_ip == ip_addr:
                    print("detect_arp reply:i am %s,my mac is %s" % (ip_addr, eth.src))
                    self.ip_to_mac[ip_addr] = eth.src
                    if arp.src_ip == web_server:
                        self.web_server_dp = datapath
                        print("i am web_server,ip is %s, derectly connect switch(dpid:%s)" % (web_server, datapath.id))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        if ev.msg.msg_len < ev.msg.total_len:
            self.logger.debug("packet truncated: only %s of %s bytes",
                              ev.msg.msg_len, ev.msg.total_len)
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src

        if(eth.ethertype == ether.ETH_TYPE_ARP):
            arp_r = pkt.get_protocol(arp.arp)
            if (arp_r.src_ip == detect_ip) or (arp_r.dst_ip == detect_ip):
                self.arp_reply_handler(
                    msg, datapath, eth, pkt.get_protocol(arp.arp))
                return

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        self.logger.info("packet in %s %s %s %s", dpid, src, dst, in_port)

        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, table_id=1,
                              idle_timeout=10, hard_timeout=10, buffer_id=msg.buffer_id)
            else:
                self.add_flow(datapath, 1, match, actions, table_id=1,
                              idle_timeout=10, hard_timeout=10)
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        if (out_port == ofproto.OFPP_FLOOD) or (msg.buffer_id == ofproto.OFP_NO_BUFFER):
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions, data=data)
            datapath.send_msg(out)
        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)
        if pkt_ipv4:
            if(pkt_ipv4.proto == inet.IPPROTO_TCP):
                pkt_tcp = pkt.get_protocol(tcp.tcp)
                if 80 == pkt_tcp.dst_port and "10.0.0.2"==pkt_ipv4.dst and "10.0.0.1"==pkt_ipv4.src:
                    print(" tcp dst_prot~~~~~~~~~~~~~%d" %pkt_tcp.dst_port)
                    # 如果是第一次访问 将机器设为已经被限制
                    if (pkt_ipv4.src, pkt_ipv4.dst, pkt_tcp.dst_port) not in self.limit.keys():
                        self.limit[
                            (pkt_ipv4.src, pkt_ipv4.dst, pkt_tcp.dst_port)] = True
                    if self.limit[(pkt_ipv4.src, pkt_ipv4.dst, pkt_tcp.dst_port)]:
                        # 如果在限制列表里面 就将该机器ban掉
                        hub.spawn(self._monitor, datapath, pkt_ipv4, pkt_tcp)

    # 将机器ban掉60秒
    def _monitor(self, datapath, pkt_ipv4, pkt_tcp):
        hub.sleep(10)
        ipv4_src = pkt_ipv4.src
        ipv4_dst = pkt_ipv4.dst
        dst_port = pkt_tcp.dst_port
        src_port = pkt_tcp.src_port
        parser = datapath.ofproto_parser
        actions = []
        match = parser.OFPMatch(
            eth_type=ether.ETH_TYPE_IP, ipv4_src=ipv4_src, ipv4_dst=ipv4_dst, ip_proto=inet.IPPROTO_TCP, tcp_dst=dst_port)
        self.add_flow(datapath, 10, match, actions,
                      idle_timeout=60, hard_timeout=60)
        match = parser.OFPMatch(
            eth_type=ether.ETH_TYPE_IP, ipv4_src=ipv4_dst, ipv4_dst=ipv4_src, ip_proto=inet.IPPROTO_TCP, tcp_src=src_port)
        self.add_flow(datapath, 10, match, actions,
                      idle_timeout=60, hard_timeout=60)
        print("add drop flow ~~~~~~~~")
