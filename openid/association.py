import random
import urllib
import time

from openid.errors import ProtocolError

from openid.util import (DiffieHellman, long2a, to_b64, parsekv,
                         from_b64, a2long, sha1, strxor)

class Association(object):
    @classmethod
    def from_expires_in(cls, expires_in, *args, **kwargs):
        kwargs['issued'] = int(time.time())
        kwargs['lifetime'] = int(expires_in)
        return cls(*args, **kwargs)

    def __init__(self, handle, secret, issued, lifetime):
        self.handle = str(handle)
        self.secret = str(secret)
        self.issued = int(issued)
        self.lifetime = int(lifetime)

    def get_expires_in(self):
        return max(0, self.issued + self.lifetime - int(time.time()))

    expires_in = property(get_expires_in)

    def __eq__(self, other):
        return self.__dict__ == other.__dict__

    def __ne__(self, other):
        return self.__dict__ != other.__dict__

class ConsumerAssociation(Association):
    def __init__(self, server_url, *args, **kwargs):
        Association.__init__(self, *args, **kwargs)
        self.server_url = str(server_url)

class AssociationManager(object):
    """Base class for type unification of Association Managers.  Most
    implementations of this should extend the BaseAssociationManager
    class below."""
    def get_association(self, server_url, assoc_handle):
        raise NotImplementedError
    
    def associate(self, server_url):
        raise NotImplementedError
    
    def invalidate(self, server_url, assoc_handle):
        raise NotImplementedError


class DumbAssociationManager(AssociationManager):
    """Using this class will cause a consumer to behave in dumb mode."""
    def get_association(self, server_url, assoc_handle):
        return None
    
    def associate(self, server_url):
        return None
    
    def invalidate(self, server_url, assoc_handle):
        pass


class BaseAssociationManager(AssociationManager):
    """Abstract base class for association manager implementations."""

    def __init__(self, associator):
        self.associator = associator

    def associate(self, server_url):
        """Returns assoc_handle associated with server_url"""
        expired = []
        assoc = None
        for current in self.get_all(server_url):
            if current.expires_in <= 0:
                expired.append(current)
            elif assoc is None:
                assoc = current

        new_assoc = None
        if assoc is None:
            assoc = new_assoc = self.associator.associate(server_url)

        if new_assoc or expired:
            self.update(new_assoc, expired)
        
        return assoc.handle

    def get_association(self, server_url, assoc_handle):
        # Find the secret matching server_url and assoc_handle
        associations = self.get_all(server_url)
        for assoc in associations:
            if assoc.handle == assoc_handle:
                return assoc

        return None

    # Subclass need to implement the rest of this classes methods.
    def update(self, new_assoc, expired):
        """new_assoc is either a new association object or None.
        Expired is a possibly empty list of expired associations.
        Subclasses should add new_assoc if it is not None and expire
        each association in the expired list."""
        raise NotImplementedError
    
    def get_all(self, server_url):
        """Subclasses should return a list of Association objects
        whose server_url attribute is equal to server_url."""
        raise NotImplementedError

    def invalidate(self, server_url, assoc_handle):
        """Subclasses should remove the association for the given
        server_url and assoc_handle from their stores."""
        raise NotImplementedError


class DiffieHelmanAssociator(object):
    def __init__(self, http_client, srand=None):
        self.http_client = http_client
        self.srand = srand or random.SystemRandom()

    def get_mod_gen(self):
        """-> (modulus, generator) for Diffie-Helman

        override this function to use different values"""
        return (DiffieHellman.DEFAULT_MOD, DiffieHellman.DEFAULT_GEN)

    def associate(self, server_url):
        p, g = self.get_mod_gen()
        dh = DiffieHellman(p, g, srand=self.srand)
        cpub = dh.createKeyExchange()

        args = {
            'openid.mode': 'associate',
            'openid.assoc_type':'HMAC-SHA1',
            'openid.session_type':'DH-SHA1',
            'openid.dh_modulus': to_b64(long2a(dh.p)),
            'openid.dh_gen': to_b64(long2a(dh.g)),
            'openid.dh_consumer_public': to_b64(long2a(cpub)),
            }

        body = urllib.urlencode(args)

        url, data = self.http_client.post(server_url, body)
        results = parsekv(data)

        def getResult(key):
            try:
                return results[key]
            except KeyError:
                raise ProtocolError(
                    'Association server response missing argument %r:\n%r'
                    % (key, data))
            
        assoc_type = getResult('assoc_type')
        if assoc_type != 'HMAC-SHA1':
            raise RuntimeError("Unknown association type: %r" % (assoc_type,))
        
        assoc_handle = getResult('assoc_handle')
        expires_in = results.get('expires_in', '0')

        session_type = results.get('session_type')
        if session_type is None:
            secret = from_b64(getResult('mac_key'))
        else:
            if session_type != 'DH-SHA1':
                raise RuntimeError("Unknown Session Type: %r"
                                   % (session_type,))

            spub = a2long(from_b64(getResult('dh_server_public')))
            dh_shared = dh.decryptKeyExchange(spub)
            enc_mac_key = getResult('enc_mac_key')
            secret = strxor(from_b64(enc_mac_key), sha1(long2a(dh_shared)))

        return ConsumerAssociation.from_expires_in(
            expires_in, server_url, assoc_handle, secret)