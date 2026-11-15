"""Fresh installation registry and IAM-only exact-identity owner bootstrap.

Nothing in this module is an HTTP bootstrap endpoint. The operator must hold
their own project's IAM authority; public registration always remains PENDING.
"""
from ..auth_preview.mail import normalize_email
from ..auth_preview.service import fail,public_user
from ..firebase_preview.service import FirebaseService
from .config import IndependentFirebaseConfig


class IndependentService(FirebaseService):
    def __init__(self,config,gateway,*,database=None):
        if not isinstance(config,IndependentFirebaseConfig) or gateway.config!=config:
            raise ValueError('Independent service requires the matching official project configuration')
        config.guard()
        super().__init__(config,gateway,database=database)
        if (not getattr(self.db,'durable',False) or self.db.prefix!=config.firestore_prefix or
            self.db.client.project!=config.firestore_project or self.db.credential_authority!='firebase-v1'):
            raise ValueError('Independent service requires its matching durable Firebase account store')
        self.initialize_installation()

    def initialize_installation(self):
        instance=self.config.instance
        expected=dict(project_id=instance.project_id,installation_id=instance.installation_id,
            organization_id=instance.organization_id)
        with self.db.transaction() as db:
            marker=db.raw('meta','independent-installation')
            if marker:
                if marker!=expected:raise ValueError('Cannot adopt another installation account namespace')
                return
            if any(db.scan(table,limit=1) for table in ('users','organizations','public_signup','firebase_identities','sessions')):
                raise ValueError('Independent installation requires an empty account namespace')
            db.execute('INSERT INTO organizations(id,name) VALUES(?,?)',(instance.organization_id,instance.institution_name.strip()))
            db.execute('INSERT INTO public_signup VALUES(1,?)',(instance.organization_id,))
            db.put('meta','independent-installation',expected)
            self.audit(db,'iam-operator','independent_installation_initialized',instance.installation_id)

    def registration_organization(self,db,requested_org_id):
        org=db.execute('SELECT * FROM organizations WHERE id=? AND enabled=1',(requested_org_id,)).fetchone()
        if not org:fail(400,'Select an institution currently accepting registration.')
        return org

    def signup_institutions(self):
        with self.db.transaction() as db:
            return [dict(id=org['id'],name=org['name']) for org in db.execute('SELECT * FROM organizations ORDER BY name') if org['enabled']]

    def bootstrap(self,*args,**kwargs):
        raise ValueError('Independent owners require the IAM operator exact verified Firebase identity bootstrap')


def bootstrap_owner(service,identity):
    """Promote one already registered, verified, exact UID/email atomically.

    Caller identity is not trusted: the configured official gateway independently
    rechecks the current provider account before the durable grant transaction.
    The immutable UID/installation marker prevents identity takeover. Its email
    is contact metadata and can follow that same UID's verified provider change.
    """
    if not isinstance(service,IndependentService):raise ValueError('Independent IAM bootstrap service required')
    service.config.guard()
    if not isinstance(identity,dict) or not identity.get('uid') or identity.get('email_verified') is not True:
        raise ValueError('An exact verified Firebase UID and email are required')
    uid=identity['uid'];email=normalize_email(identity.get('email',''))
    proof=service.gateway.lookup_owner(uid,email)
    if (proof.get('uid')!=uid or normalize_email(proof.get('email',''))!=email or
        proof.get('email_verified') is not True or proof.get('disabled') is not False):
        raise ValueError('Firebase identity must currently be enabled and email verified')
    expected=dict(uid=uid,email=email,installation_id=service.config.instance.installation_id)
    with service.db.transaction() as db:
        marker=db.raw('meta','independent-owner')
        if marker and (marker.get('uid')!=uid or marker.get('installation_id')!=expected['installation_id']):
            raise ValueError('This installation already has a different bootstrapped owner')
        binding=db.raw('firebase_identities',uid)
        user=db.raw('users',binding['user_id']) if binding else None
        if (not user or normalize_email(user.get('email',''))!=email or not user.get('email_verified_at') or
            user.get('password_hash')!='FIREBASE_MANAGED'):
            raise ValueError('Register and verify this exact Firebase identity in the application first')
        if marker:
            if user['role']!='PLATFORM_OWNER' or user['status']!='ACTIVE':raise ValueError('Bootstrapped owner state requires operator review')
            if marker.get('email')!=email:
                db.put('meta','independent-owner',expected)
                service.audit(db,'iam-operator','independent_owner_contact_updated',user['id'],uid=uid,
                    installation_id=service.config.instance.installation_id)
            return public_user(user)
        if db.execute("SELECT 1 FROM users WHERE role='PLATFORM_OWNER'").fetchone():
            raise ValueError('An owner already exists; ownership transfer requires separate review')
        if (user['status']!='PENDING' or user['role']!='PENDING' or user.get('org_id') or
            user.get('requested_org_id')!=service.config.instance.organization_id):
            raise ValueError('Bootstrap requires an unapproved pending account in the initial institution')
        changed=db.execute('UPDATE users SET status=?,role=?,org_id=?,version=version+1 WHERE id=? AND version=?',
            ('ACTIVE','PLATFORM_OWNER',service.config.instance.organization_id,user['id'],user['version']))
        if changed.rowcount!=1:raise ValueError('Owner account changed concurrently; retry after review')
        service.revoke(db,user['id'])
        db.put('meta','independent-owner',expected)
        service.audit(db,'iam-operator','independent_owner_bootstrapped',user['id'],uid=uid,
            installation_id=service.config.instance.installation_id)
        return public_user(db.raw('users',user['id']))
