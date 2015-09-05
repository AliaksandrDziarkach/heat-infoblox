# Copyright (c) 2015 Infoblox Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import logging
import netaddr

from heat.common.i18n import _
from heat.engine import attributes
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine import support

from heat_infoblox import resource_utils


LOG = logging.getLogger(__name__)


class GridMember(resource.Resource):
    '''A resource which represents an Infoblox Grid Member.

    This is used to provision new grid members on an existing grid. See the
    Grid Master resource to create a new grid.
    '''

    PROPERTIES = (
        WAPI_URL, WAPI_USERNAME, WAPI_PASSWORD,
        WAPI_NOSSLVERIFY, WAPI_CERTIFICATE,
        NAME, MODEL, LICENSES, TEMP_LICENSES, REMOTE_CONSOLE,
        MGMT_PORT, LAN1_PORT, LAN2_PORT, HA_PORT,
        GM_IP, GM_CERTIFICATE, 
        NAT_IP
    ) = (
        'wapi_url', 'wapi_username', 'wapi_password',
        'wapi_insecure_do_not_verify_certificate', 'wapi_certificate',
        'name', 'model', 'licenses', 'temp_licenses', 'remote_console_enabled',
        'MGMT', 'LAN1', 'LAN2', 'HA',
        'gm_ip', 'gm_certificate',
        'nat_ip'
    )

    ATTRIBUTES = (
        JOIN_TOKEN, USER_DATA
    ) = (
        'grid_join_token', 'user_data'
    )

    support_status = support.SupportStatus(support.UNSUPPORTED)

    properties_schema = {
        WAPI_URL: properties.Schema(
            properties.Schema.STRING,
            _('URL to the Infoblox WAPI.'),
            required=True
        ),
        WAPI_CERTIFICATE: properties.Schema(
            properties.Schema.STRING,
            _('The certificate for validation of the WAPI URL.'),
            required=False
        ),
        WAPI_NOSSLVERIFY: properties.Schema(
            properties.Schema.BOOLEAN,
            _('Do not require the certificate for validating the WAPI URL. '
              'This is NOT SECURE and should not be used in a production '
              'environment.'),
            default=False,
            required=False
        ),
        WAPI_USERNAME: properties.Schema(
            properties.Schema.STRING,
            _('Username to login to the WAPI.'),
            required=True
        ),
        WAPI_PASSWORD: properties.Schema(
            properties.Schema.STRING,
            _('Password to login to the WAPI.'),
            required=True
        ),
        NAME: properties.Schema(
            properties.Schema.STRING,
            _('Member name.'),
        ),
        MODEL: properties.Schema(
            properties.Schema.STRING,
            _('Infoblox model name.'),
        ),
        LICENSES: properties.Schema(
            properties.Schema.LIST,
            _('List of licenses to pre-provision.'),
            schema=properties.Schema(
                properties.Schema.STRING
            )
        ),
        TEMP_LICENSES: properties.Schema(
            properties.Schema.LIST,
            _('List of temporary licenses to apply to the member.'),
            schema=properties.Schema(
                properties.Schema.STRING
            )
        ),
        REMOTE_CONSOLE: properties.Schema(
            properties.Schema.BOOLEAN,
            _('Enable the remote console.')
        ),
        GM_IP: properties.Schema(
            properties.Schema.STRING,
            _('The Gridmaster IP address.'),
            required=True
        ),
        GM_CERTIFICATE: properties.Schema(
            properties.Schema.STRING,
            _('The Gridmaster SSL certificate for verification.'),
            required=False
        ),
        NAT_IP: properties.Schema(
            properties.Schema.STRING,
            _('If the GM will see this member as a NATed address, enter that '
              'address here. Or, enter a the name or ID of the external network'
              ' through which the GM will be accessed, and the NAT IP will be '
              'looked up in the Neutron router for the LAN1 port.'),
            required=False
        ),
        MGMT_PORT: resource_utils.port_schema(MGMT_PORT, False),
        LAN1_PORT: resource_utils.port_schema(LAN1_PORT, True),
    }

    attributes_schema = {
        JOIN_TOKEN: attributes.Schema(
            _('Grid join authentication token.'),
            type=attributes.Schema.STRING),
        USER_DATA: attributes.Schema(
            _('User data for the Nova boot process.'),
            type=attributes.Schema.STRING)
    }

    def _make_network_settings(self, ip):
        subnet = self.client('neutron').show_subnet(ip['subnet_id'])['subnet']
        ipnet = netaddr.IPNetwork(subnet['cidr'])
        return {'address': ip['ip_address'], 'subnet_mask': str(ipnet.netmask),
            'gateway': subnet['gateway_ip']}

    def _make_ipv6_settings(self, ip):
        subnet = self.client('neutron').show_subnet(ip['subnet_id'])['subnet']
        autocfg = (subnet['ipv6_ra_mode'] != "stateful")
        return {'virtual_ip': ip['ip_address'], 'cidr_prefix': subnet['cidr'],
            'gateway': subnet['gateway_ip'], 'enabled': True, 
            'auto_router_config_enabled': autocfg }
    
    def infoblox(self):
        if not getattr(self,'infoblox_object', None):
            self.infoblox_object = resource_utils.connect_to_infoblox(
                self.properties[self.WAPI_URL],
                self.properties[self.WAPI_USERNAME],
                self.properties[self.WAPI_PASSWORD],
                not self.properties[self.WAPI_NOSSLVERIFY],
                self.properties[self.WAPI_CERTIFICATE])
        return self.infoblox_object

    def handle_create(self):
        port = self.client('neutron').show_port(
            self.properties[self.LAN1_PORT])['port']
        ipv4 = None
        ipv6 = None
        for ip in port['fixed_ips']:
            if ':' in ip['ip_address'] and ipv6 is None:
                ipv6 = self._make_ipv6_settings(ip)
            else:
                if ipv4 is None:
                    ipv4 = self._make_network_settings(ip)

        LOG.debug("ipv4 = %s, ipv6 = %s" % (ipv4, ipv6))
        #TODO(jbelamaric): fix IPv6
        ipv6 = None

        name = self.properties[self.NAME]
        nat = self.properties[self.NAT_IP]
        if nat == "AUTO":
            raise ValueError("AUTO is not yet supported for %s." % self.NAT_IP)
        self.infoblox().create_member(name=name, ipv4=ipv4, ipv6=ipv6, nat_ip=nat)
        self.infoblox().pre_provision_member(name,
            hwmodel=self.properties[self.MODEL], hwtype='IB-VNIOS',
            licenses=self.properties[self.LICENSES])

        self.resource_id_set(name)

    def _make_user_data(self, member, token):
        user_data = '#infoblox-config\n\n'

        temp_licenses = self.properties[self.TEMP_LICENSES]
        if temp_licenses and len(temp_licenses) > 0:
            user_data += 'temp_license: %s\n' % ','.join(temp_licenses)

        remote_console = self.properties[self.REMOTE_CONSOLE]
        if remote_console is not None:
            user_data += 'remote_console_enabled: %s\n' % remote_console

        vip = member['vip_setting']
        if vip:
            user_data += 'lan1:\n'
            user_data += '  v4_addr: %s\n' % vip['address']
            user_data += '  v4_netmask: %s\n' % vip['subnet_mask']
            user_data += '  v4_gw: %s\n' % vip['gateway']

        if token and len(token) > 0:
            user_data += 'gridmaster:\n'
            user_data += '  token: %s\n' % token[0]['token']
            user_data += '  ip_addr: %s\n' % self.properties[self.GM_IP]
            user_data += '  certificate: %s\n' % self.properties[self.GM_CERTIFICATE]

        return user_data

    def _get_member_tokens(self, member):
        token = self.infoblox().connector.call_func('read_token',
                        member['_ref'], {})['pnode_tokens']
        if len(token) == 0:
            self.infoblox().connector.call_func('create_token',
                        member['_ref'], {})['pnode_tokens']
            token = self.infoblox().connector.call_func('read_token',
                        member['_ref'], {})['pnode_tokens']
        return token

    def _resolve_attribute(self, name):
        member_name = self.properties[self.NAME]
        member = self.infoblox().get_member(member_name,
            return_fields=['vip_setting', 'ipv6_setting'])[0]
        token = self._get_member_tokens(member)
        LOG.debug("MEMBER for %s = %s" % (name, member))
        if name == self.JOIN_TOKEN:
            return token
        if name == self.USER_DATA:
            return self._make_user_data(member, token)
        return None

def resource_mapping():
    return {
        'Infoblox::Grid::Member': GridMember,
    }