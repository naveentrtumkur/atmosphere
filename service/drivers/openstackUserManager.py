import os

from keystoneclient.exceptions import NotFound, ClientException
from keystoneclient.v3 import client as ks_client
from novaclient import client as nova_client

from atmosphere.logger import logger
from service.drivers.common import _connect_to_keystone, _connect_to_nova, find
"""
OpenStack CloudAdmin Libarary
    Use this library to:
    * manage users within Keystone - openstack auth
"""


class UserManager():
    keystone = None
    nova = None
    user = None
    password = None
    tenant = None

    @classmethod
    def lc_driver_init(self, lc_driver, region, *args, **kwargs):
        lc_driver_args = {
            'username': lc_driver.key,
            'password': lc_driver.secret,
            'tenant_name': lc_driver._ex_tenant_name,
            'auth_url': lc_driver._ex_force_auth_url,
            'region_name': region
        }
        lc_driver_args.update(kwargs)
        manager = UserManager(*args, **lc_driver_args)
        return manager

    def __init__(self, *args, **kwargs):
        self.newConnection(*args, **kwargs)

    def newConnection(self, *args, **kwargs):
        self.keystone = _connect_to_keystone(*args, **kwargs)
        self.nova = _connect_to_nova(*args, **kwargs)


    ##Composite Classes##
    def add_usergroup(self, username, password,
                     createUser=True, adminRole=False):
        """
        Create a group for this user only
        then create the user
        """
        #Create tenant for user/group
        tenant = self.add_tenant(username)

        #Create user
        try:
            user = self.add_user(username, password, tenant.name)
        except ClientException as user_exists:
            logger.debug('Received Error %s on add, User exists.' %
                         user_exists)
            user = self.get_user(username)

        logger.debug("Assign Tenant:%s Member:%s Role:%s" %
                    (username, username, adminRole))
        try:
            role = self.add_tenant_member(username, username, adminRole)
        except ClientException:
            logger.warn('Could not assign role to username %s' % username)
        try:
            # keystone admin always gets access, always has admin priv.
            self.add_tenant_member(username, self.keystone.username, True)
        except ClientException:
            logger.warn('Could not assign admin role to username %s' %
                        self.keystone.username)
        return (tenant, user, role)

    def build_security_group(self, username, password, tenant_name,
            protocol_list=None, *args, **kwargs):

        nova = nova_client.Client(username,
                                  password,
                                  tenant_name,
                                  self.nova.client.auth_url,
                                  self.nova.client.region_name,
                                  *args, no_cache=True, **kwargs)
        nova.client.region_name = self.nova.client.region_name
        if not protocol_list:
            #Build a "good" one.
            protocol_list = [
                ('TCP', 22, 22),
                ('TCP', 80, 80),
                ('TCP', 4200, 4200),
                ('TCP', 5500, 5500),
                ('TCP', 5666, 5666),
                ('TCP', 5900, 5904),
                ('TCP', 5900, 5999), # TEMP
                ('TCP', 9418, 9418),
                ('ICMP', -1, -1),
            ]
        #with nova.security_groups.find(name='default') as default_sec_group:
        default_sec_group = nova.security_groups.find(name='default')
        for (ip_protocol, from_port, to_port) in protocol_list:
            if not self.find_rule(default_sec_group, ip_protocol,
                    from_port, to_port):
                nova.security_group_rules.create(default_sec_group.id,
                                                 ip_protocol=ip_protocol,
                                                 from_port=from_port,
                                                 to_port=to_port)
        return nova.security_groups.find(name='default')

    def find_rule(self, security_group, ip_protocol, from_port, to_port):
        for r in security_group.rules:
            if r['from_port'] == from_port\
            and r['to_port'] == to_port\
            and r['ip_protocol'] == ip_protocol:
                return True
        return False

    def get_usergroup(self, username):
        return self.get_tenant(username)

    def delete_usergroup(self, username, deleteUser=True):
        try:
            self.delete_tenant_member(username, username, True)
        except ClientException:
            logger.warn('Could not remove admin role from username %s' %
                        username)
        try:
            self.delete_tenant_member(username, username, False)
        except ClientException:
            logger.warn('Could not remove normal role from username %s' %
                        username)
        try:
            self.delete_tenant_member(username, self.keystone.username, True)
        except ClientException:
            logger.warn('Could not remove role from keystone user %s' %
                        self.keystone.username)

        if deleteUser:
            self.delete_user(username)
        self.delete_tenant(username)

    ##ADD##
    def add_role(self, rolename):
        """
        Create a new role
        """
        return self.keystone.roles.create(name=rolename)

    def add_tenant(self, groupname):
        """
        Create a new tenant
        """
        try:
            return self.keystone.tenants.create(groupname)
        except Exception, e:
            logger.exception(e)
            raise

    def add_tenant_member(self, groupname, username, adminRole=False):
        """
        Adds user to group
        Invalid groupname, username, rolename :
            raise keystoneclient.exceptions.NotFound
        """
        tenant = self.get_tenant(groupname)
        user = self.get_user(username)
        #Only supporting two roles..
        if adminRole:
            role = self.get_role('admin')
        else:
            role = self.get_role('defaultMemberRole')
        try:
            return tenant.add_user(user, role)
        except Exception, e:
            logger.exception(e)
            raise

    def add_user(self, username, password=None, groupname=None):
        """
        Create a new user
        Invalid groupname : raise keystoneclient.exceptions.NotFound
        """
        kwargs = {
            'name': username,
            'password': password,
            'email': '%s@iplantcollaborative.org' % username,
        }
        if groupname:
            try:
                tenant = self.get_tenant(groupname)
                kwargs['tenant_id'] = tenant.id
            except NotFound:
                logger.warn("User %s does not exist" % username)
                raise
        return self.keystone.users.create(**kwargs)

    ##DELETE##
    def delete_role(self, rolename):
        """
        Retrieve,Delete the user
        Invalid username : raise keystoneclient.exceptions.NotFound
        """
        role = self.get_role(rolename)
        if role:
            role.delete()
        return True

    def delete_tenant(self, groupname):
        """
        Retrieve and delete the tenant/group matching groupname
        Returns True on success
        Invalid groupname : raise keystoneclient.exceptions.NotFound
        """
        tenant = self.get_tenant(groupname)
        if tenant:
            tenant.delete()
        return True

    def delete_tenant_member(self, groupname, username, adminRole=False):
        """
        Retrieves the tenant and user object
        Removes user of the admin/member role
        Returns True on success
        Invalid username, groupname, rolename:
            raise keystoneclient.exceptions.NotFound
        """
        tenant = self.get_tenant(groupname)
        user = self.get_user(username)
        if adminRole:
            role = self.get_role('admin')
        else:
            role = self.get_role('defaultMemberRole')
        if not tenant or not user:
            return True
        try:
            tenant.remove_user(user, role)
            return True
        except NotFound as no_role_for_user:
            logger.debug('Error - %s: User-role combination does not exist' %
                         no_role_for_user)
            return True
        except Exception, e:
            logger.exception(e)
            raise

    def delete_user(self, username):
        """
        Retrieve,Delete the user
        Invalid username : raise keystoneclient.exceptions.NotFound
        """
        user = self.get_user(username)
        if user:
            user.delete()
        return True

    def get_role(self, rolename):
        """
        Retrieve role
        Invalid rolename : raise keystoneclient.exceptions.NotFound
        """
        try:
            return find(self.keystone.roles, name=rolename)
        except NotFound:
            return None

    def get_tenant(self, groupname):
        """
        Retrieve tenant
        Invalid groupname : raise keystoneclient.exceptions.NotFound
        """
        try:
            return find(self.keystone.tenants, name=groupname)
        except NotFound:
            return None

    def get_user(self, username):
        """
        Retrieve user
        Invalid username : raise keystoneclient.exceptions.NotFound
        """
        try:
            return find(self.keystone.users, name=username)
        except NotFound:
            return None

    def list_roles(self):
        return self.keystone.roles.list()

    def list_tenants(self):
        return self.keystone.tenants.list()

    def list_users(self):
        return self.keystone.users.list()

