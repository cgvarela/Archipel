# -*- coding: utf-8 -*-
#
# NuageNetwork.py
#
# Copyright (C) 2010 Antoine Mercadal <antoine.mercadal@inframonde.eu>
# Copyright, 2011 - Franck Villaume <franck.villaume@trivialdev.com>
# This file is part of ArchipelProject
# http://archipelproject.org
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sqlite3

from archipel.archipelHypervisor import TNArchipelHypervisor
from archipel.archipelHypervisor import TNArchipelVirtualMachine
from archipelcore import xmpp
from archipelcore.archipelPlugin import TNArchipelPlugin
from archipelcore.utils import build_error_iq


ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK = "archipel:hypervisor:nuage:network"
ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_CREATE = -12001
ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_DELETE = -12002
ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_UPDATE = -12003
ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_GET = -12004
ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_GETNAMES = -12005

# This is a sample of the definition of a nuage network
# <nuage_network name="blabla" type="ipv4" domain="domain1" zone="zone2">
#     <bandwidth>
#         <inbound average='1000' peak='5000' burst='5120' />
#         <outbound average='1000' peak='5000' burst='5120' />
#     </bandwidth>
#     <subnet address="192.168.122.1" netmask="255.255.255.0" gateway="192.168.122.2" />
# </nuage_network>


class TNHypervisorNuageNetworks (TNArchipelPlugin):

    def __init__(self, configuration, entity, entry_point_group):
        """
        Initialize the plugin.
        @type configuration: Configuration object
        @param configuration: the configuration
        @type entity: L{TNArchipelEntity}
        @param entity: the entity that owns the plugin
        @type entry_point_group: string
        @param entry_point_group: the group name of plugin entry_point
        """
        TNArchipelPlugin.__init__(self, configuration=configuration, entity=entity, entry_point_group=entry_point_group)
        self.nuageBridgeName = self.configuration.get("NUAGE", "nuage_bridge")
        self.database = None

        # permissions
        self.entity.permission_center.create_permission("nuagenetwork_get", "Authorizes user to get the existing Nuage networks", False)
        self.entity.permission_center.create_permission("nuagenetwork_getnames", "Authorizes user to get a Nuage network", False)

        if isinstance(self.entity, TNArchipelHypervisor):
            self.entity.permission_center.create_permission("nuagenetwork_create", "Authorizes user to create a Nuage network", False)
            self.entity.permission_center.create_permission("nuagenetwork_delete", "Authorizes user to delete a Nuage network", False)
            self.entity.permission_center.create_permission("nuagenetwork_update", "Authorizes user to update a Nuage network", False)

        if isinstance(self.entity, TNArchipelHypervisor):
            self.manage_database()

        if isinstance(self.entity, TNArchipelVirtualMachine):
            self.entity.add_vm_definition_hook(self.update_vm_xml_hook)


    ### Plugin implementation

    def register_handlers(self):
        """
        This method will be called by the plugin user when it will be
        necessary to register module for listening to stanza.
        """
        if isinstance(self.entity, TNArchipelVirtualMachine):
            self.entity.xmppclient.RegisterHandler('iq', self.process_iq_for_virtualmachine, ns=ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK)
        elif isinstance(self.entity, TNArchipelHypervisor):
            self.entity.xmppclient.RegisterHandler('iq', self.process_iq_for_hypervisor, ns=ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK)

    def unregister_handlers(self):
        """
        Unregister the handlers.
        """
        if isinstance(self.entity, TNArchipelVirtualMachine):
            self.entity.xmppclient.UnregisterHandler('iq', self.process_iq_for_virtualmachine, ns=ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK)
        elif isinstance(self.entity, TNArchipelHypervisor):
            self.entity.xmppclient.UnregisterHandler('iq', self.process_iq_for_hypervisor, ns=ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK)

    @staticmethod
    def plugin_info():
        """
        Return informations about the plugin.
        @rtype: dict
        @return: dictionary contaning plugin informations
        """
        plugin_friendly_name = "Hypervisor Nuage Networks"
        plugin_identifier = "hypervisor_nuage_network"
        plugin_configuration_section = "NUAGE"
        plugin_configuration_tokens = ["nuage_bridge"]
        return {"common-name": plugin_friendly_name,
                "identifier": plugin_identifier,
                "configuration-section": plugin_configuration_section,
                "configuration-tokens": plugin_configuration_tokens}



    ### VM XML Desc update
    def update_vm_xml_hook(self, senderJID, vm_xml_node):
        """
        Update the VM definition to insert the metadata informations
        @type sender: xmpp.JID
        @param sender: The JID of the sender
        @type vm_xml_node: xmpp.Node
        @param vm_xml_node: The VM's XML description
        """
        if not self.entity.vcard_infos:
            return vm_xml_node

        if not vm_xml_node.getTag("metadata"):
            vm_xml_node.addChild("metadata")

        # before removing everything, we need to to check if we already have some
        # metadata about the networks, then we store the values in order to manage
        # the nuage network correctly
        old_nuage_networks_infos = {}

        # If we already have some existing NUAGE NETWORKS metadata, then we store old informations
        if vm_xml_node.getTag("metadata").getTag("nuage"):
            # We check that we had some interfaces attached in some eventual nuage networks
            nuage_networks_nodes = vm_xml_node.getTag("metadata").getTag("nuage").getTags("nuage_network")

            if nuage_networks_nodes:
                # We have :) so let's parse this information

                for nuage_network in nuage_networks_nodes:

                    nuage_network_interface = nuage_network.getTag("interface")

                    # we check that we have a mac address. (We should always have, but it's client dependent, so avoid a crash here)
                    if nuage_network_interface and nuage_network_interface.getAttr("mac"):
                        # Here, we store old information (nuage network name used by the interface
                        # and eventual desired IP address) in to a dictionary named old_nuage_networks_infos
                        # and we put as key the MAC address, and as value a tupple containing nuage network name and eventual IP
                        macaddress = nuage_network_interface.getAttr("mac").lower()
                        nuagenetworkname = nuage_network.getAttr("name")
                        interface_ip = nuage_network_interface.getAttr("address")
                        old_nuage_networks_infos[macaddress] = (nuagenetworkname, interface_ip)


        # Now we have old metadata in memory, we remove the nuage node. in order to rebuild it
        if vm_xml_node.getTag("metadata").getTag("nuage"):
            vm_xml_node.getTag("metadata").delChild("nuage")

        # let's keep a track of the hypervisor_nuage_plugin version we will be using for getting network info
        hypervisor_nuage_plugin = self.entity.hypervisor.get_plugin("hypervisor_nuage_network")

        # We now create the root nuage metadata node, and we put common information coming from the VM's VCARD
        nuage_node = xmpp.Node("nuage", attrs={"xmlns": "http://www.nuagenetworks.net/2013/Vm/Metadata"})
        nuage_node.addChild("user", attrs={"name": self.entity.vcard_infos["USERID"]})
        nuage_node.addChild("enterprise", attrs={"name": self.entity.vcard_infos["ORGNAME"]})
        nuage_node.addChild("application", attrs={"name": self.entity.vcard_infos["CATEGORIES"]})

        # Now we will parse all given interfaces, and build nuage_networks info in nuage metadata
        if not vm_xml_node.getTag("devices", {}).getTags("interface"):
            return vm_xml_node

        interface_nodes = vm_xml_node.getTag("devices").getTags("interface")

        for interface in interface_nodes:

            if interface.getAttr("type") == "nuage":
                # Ok, here is how it works:
                # If the interface type is nuage, then it means that we just received a define from the client
                # and we assume than the network name and interface ip are given in the XML description
                network_name    = interface.getAttr("nuage_network_name")
                ip_address      = interface.getAttr("nuage_network_interface_ip")
                mac_address     = interface.getTag("mac").getAttr("address")

            else:
                # Now, if the interface is not a nuage interface, we first check if
                # we have some old nuage networks attached to the interface MAC
                interface_mac_address = None
                if interface.getTag("mac"):
                    interface_mac_address = interface.getTag("mac").getAttr("address")

                if interface_mac_address in old_nuage_networks_infos:
                    # Ok now. We have an old nuage network attached to the MAC address
                    # But we need to check the target dev name is using the NUAGE NETWORKS form
                    # i.e. dev_name == mac_address.replace(":", "")
                    interface_target_dev = None
                    if interface.getTag("target"):
                        interface_target_dev = interface.getTag("target").getAttr("dev")

                    if not interface_target_dev == interface_mac_address.replace(":", ""):
                        # Ok, so the dev name is not a valid nuage name, so we assume
                        # the interface is not using a NUAGE NETWORK, we don't generate
                        # nuage metadata and we continue to parse the next interface
                        continue

                    # If we reach this block, then this means we have an old NUAGE NETWORK
                    # that was attached to the this interface's mac, AND the target dev name
                    # is using the correct NUAGE NETWORKS form, so we just get the nuage informations
                    # from the old_nuage_networks_infos that has been build above
                    mac_address     = interface.getTag("mac").getAttr("address").lower()
                    network_name    = old_nuage_networks_infos[mac_address][0]
                    ip_address      = old_nuage_networks_infos[mac_address][1]

                else:
                    # We do not have any old nuage network using this mac address, so we continue.
                    continue

            # And now, we do our basic business to generate the <metadata>

            network_name_XML    = hypervisor_nuage_plugin.get_network_by_name(network_name)
            strXML              = str(network_name_XML).replace('xmlns="archipel:hypervisor:nuage:network" ', '')
            network_name_XML    = xmpp.simplexml.NodeBuilder(data=strXML).getDom()
            interface_node      = network_name_XML.addChild("interface", attrs={"mac": mac_address})

            if ip_address:
                interface_node.setAttr("address", ip_address)

            ## Now we reconfigure the nic to be a bridge
            interface.setAttr("type", "bridge")
            if interface.getAttr("name"):
                interface.delAttr("name")
            if interface.getTag("source"):
                interface.delChild("source")
            interface.addChild("source", attrs={"bridge": self.nuageBridgeName})

            if interface.getTag("virtualport"):
                interface.delChild("virtualport")
            interface.addChild("virtualport", attrs={"type": "openvswitch"})

            if interface.getTag("target"):
                interface.delChild("target")
            interface.addChild("target", attrs={"dev": mac_address.replace(":", "")})

            nuage_node.addChild(node=network_name_XML)

        vm_xml_node.getTag("metadata").addChild(node=nuage_node)

        return vm_xml_node


    ### PubSub Management

    def manage_database(self):
        """
        Create and / or recover the parking database
        """
        self.database = sqlite3.connect(self.configuration.get("NUAGE", "database"), check_same_thread=False)
        self.database.row_factory = sqlite3.Row
        self.database.execute("create table if not exists nuagenetworks (name text unique, network string)")
        self.database.commit()

    def get_network_by_name(self, name):
        """
        Get a network from the list
        @type name: String
        @param name: The name of the network
        """
        rows = self.database.execute("select network from nuagenetworks where name=? LIMIT 1", (name,))
        return xmpp.simplexml.NodeBuilder(data=rows.fetchone()[0]).getDom()

    def get_all_networks(self):
        """
        Return all networks
        """
        rows = self.database.execute("select * from nuagenetworks")
        ret = []
        for row in rows:
            ret.append({"name": row[0], "network": xmpp.simplexml.NodeBuilder(data=row[1]).getDom()})
        return ret

    def get_all_networks_names(self):
        """
        Returns a list of all the networks names
        """
        plugin = self
        if self.entity.__class__.__name__ == "TNArchipelVirtualMachine":
            plugin = self.entity.hypervisor.get_plugin("hypervisor_nuage_network")
        rows = plugin.database.execute("select name from nuagenetworks")
        ret = []
        for row in rows:
            ret.append(row[0])
        return ret

    def get_vms_using_network_with_name(self, name):
        """
        Return the list of ArchipelVM that are using the network with given name.
        @type name: String
        @param name: The network name
        """
        vms = []
        for uuid, vm in self.entity.virtualmachines.iteritems():
            if not vm.definition:
                continue
            try:
                nuage_networks_xml = vm.definition.getTag("metadata").getTag("nuage").getTags("nuage_network")
            except:
                continue
            for nuage_network in nuage_networks_xml:
                if nuage_network.getAttr("name").upper() == name.upper():
                    vms.append(vm)
                    continue

        return vms


    def add_network(self, name, network):
        """
        Add a Network
        @type name: String
        @param name: The name of the network
        @type network: xmpp.Node
        @param network: the XML node representing the network
        """
        self.database.execute("insert into nuagenetworks values(?, ?)", (name, str(network).replace('xmlns=\"archipel:hypervisor:nuage:network\"', '')))
        self.database.commit()
        self.entity.push_change("nuagenetwork", "created")

    def delete_network(self, name):
        """
        Remove a network from the db
        """
        ## We now redifine the VMs using this domain
        vms_using_this_network = self.get_vms_using_network_with_name(name)
        if len(vms_using_this_network):
            exception_string = "Unable to delete the network. Following VMs are using it:\n"
            for vm in vms_using_this_network:
                exception_string = "%s - %s\n" % (exception_string, vm.name)
            exception_string = "%sPlease update their networks before removing it" % (exception_string)
            raise Exception(exception_string)

        self.database.execute("delete from nuagenetworks where name=?", (name,))
        self.database.commit()

        self.entity.push_change("nuagenetwork", "deleted")

    def update_network(self, name, new_network):
        """
        Update the network XML with given name
        @type name: String
        @param name: The name of the network
        @type network: xmpp.Node
        @param network: the XML node representing the new network
        """
        self.database.execute("update nuagenetworks set network=? where name=?", (str(new_network).replace('xmlns=\"archipel:hypervisor:nuage:network\"', ''), name))
        self.database.commit()

        ## We now redifine the VMs using this domain
        vms_using_this_network = self.get_vms_using_network_with_name(name)
        for vm in vms_using_this_network:
            vm.define(vm.definition)

        self.entity.push_change("nuagenetwork", "updated")

    ### XMPP Processing

    def process_iq_for_hypervisor(self, conn, iq):
        """
        This method is invoked when a ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK IQ is received.
        It understands IQ of type:
            - create
            - get
            - destroy
            - update
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        reply = None
        action = self.entity.check_acp(conn, iq)
        self.entity.check_perm(conn, iq, action, -1, prefix="nuagenetwork_")
        if action == "create":
            reply = self.iq_create(iq)
        elif action == "delete":
            reply = self.iq_delete(iq)
        elif action == "update":
            reply = self.iq_update(iq)
        elif action == "get":
            reply = self.iq_get(iq)
        elif action == "getnames":
            reply = self.iq_getnames(iq)
        if reply:
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed

    def process_iq_for_virtualmachine(self, conn, iq):
        """
        This method is invoked when a ARCHIPEL_NS_HYPERVISOR_NUAGE_NETWORK IQ is received.
        It understands IQ of type:
            - get
        @type conn: xmpp.Dispatcher
        @param conn: ths instance of the current connection that send the stanza
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        """
        reply = None
        action = self.entity.check_acp(conn, iq)
        self.entity.check_perm(conn, iq, action, -1, prefix="nuagenetwork_")
        if action == "get":
            reply = self.iq_get(iq)
        elif action == "getnames":
            reply = self.iq_getnames(iq)
        if reply:
            conn.send(reply)
            raise xmpp.protocol.NodeProcessed

    def iq_create(self, iq):
        """
        Crate a Nuage network according to the XML data
        network passed in argument.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            definition = iq.getTag("query").getTag("archipel").getTag("nuage_network")
            self.add_network(definition.getAttr("name"), definition)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_CREATE)
        return reply

    def iq_delete(self, iq):
        """
        Delete a nuage network according to name passed in argument.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            network_name = iq.getTag("query").getTag("archipel").getAttr("name")
            self.delete_network(network_name)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_DELETE)
        return reply

    def iq_update(self, iq):
        """
        Update a Nuage network.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            network_xml = iq.getTag("query").getTag("archipel").getTag("nuage_network")
            network_name = iq.getTag("query").getTag("archipel").getAttr("name")
            self.update_network(network_name, network_xml)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_UPDATE)
        return reply

    def iq_get(self, iq):
        """
        Get information of a Nuage network.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            networks = self.get_all_networks()
            nodes = []
            for network_info in networks:
                network_node = xmpp.Node("nuage")
                network_node.addChild(node=network_info["network"])
                nodes.append(network_node)
            reply.setQueryPayload(nodes)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_GET)
        return reply

    def iq_getnames(self, iq):
        """
        Get list of all Nuage networks names.
        @type iq: xmpp.Protocol.Iq
        @param iq: the received IQ
        @rtype: xmpp.Protocol.Iq
        @return: a ready to send IQ containing the result of the action
        """
        try:
            reply = iq.buildReply("result")
            networks = self.get_all_networks_names()
            nodes = []
            for network_name in networks:
                nodes.append(xmpp.Node("network", attrs={"name": network_name}))
            reply.setQueryPayload(nodes)
        except Exception as ex:
            reply = build_error_iq(self, ex, iq, ARCHIPEL_ERROR_CODE_NUAGE_NETWORKS_GETNAMES)
        return reply
