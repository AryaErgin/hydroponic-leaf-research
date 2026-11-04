from contextlib import contextmanager
from contextlib import closing
import sqlite3


def create_database(config):
    if config.firestore_project:
        from .firestore_database import FirestoreDatabase
        return FirestoreDatabase(config)
    return Database(config.database)


SCHEMA='''
CREATE TABLE IF NOT EXISTS organizations (
 id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1, approvals_enabled INTEGER NOT NULL DEFAULT 1,
 version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS public_signup (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), org_id TEXT NOT NULL REFERENCES organizations(id));
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 display_name TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','ACTIVE','REJECTED','SUSPENDED')),
 role TEXT NOT NULL CHECK(role IN ('PENDING','MEMBER','ORG_ADMIN','PLATFORM_ADMIN','PLATFORM_OWNER')),
 requested_org_id TEXT REFERENCES organizations(id), institution_request TEXT NOT NULL DEFAULT '',
 org_id TEXT REFERENCES organizations(id), created_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,
 CHECK(status != 'ACTIVE' OR (role != 'PENDING' AND org_id IS NOT NULL)));
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id), csrf TEXT NOT NULL,
 expires REAL NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS recovery (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS email_challenges (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), email TEXT NOT NULL,
 purpose TEXT NOT NULL, digest TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(user_id,purpose));
CREATE INDEX IF NOT EXISTS session_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS email_notifications (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), email TEXT NOT NULL,
 event TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, delivered REAL, last_error TEXT);
CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, resets REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_id TEXT,
 action TEXT NOT NULL, target_id TEXT NOT NULL, details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources (
 id TEXT PRIMARY KEY, org_id TEXT REFERENCES organizations(id), kind TEXT NOT NULL,
 plant_id TEXT REFERENCES resources(id), payload TEXT NOT NULL,
 artifact_key TEXT, artifact_hash TEXT, artifact_type TEXT);
CREATE INDEX IF NOT EXISTS resources_tenant ON resources(org_id,kind);
CREATE TRIGGER IF NOT EXISTS immutable_resource_tenant BEFORE UPDATE OF org_id ON resources
 WHEN OLD.org_id IS NOT NEW.org_id BEGIN SELECT RAISE(ABORT,'resource organization is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_organization_id BEFORE UPDATE OF id ON organizations
 BEGIN SELECT RAISE(ABORT,'organization ID is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit
 BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit
 BEGIN SELECT RAISE(ABORT,'audit is append only'); END;
'''


class Database:
    def __init__(self,path):
        self.path=path
        with closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            columns={r['name'] for r in db.execute('PRAGMA table_info(users)')}
            # Additive migration of this candidate's isolated local database only.
            if 'email' not in columns:db.execute('ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE')
            if 'email_verified_at' not in columns:db.execute('ALTER TABLE users ADD COLUMN email_verified_at TEXT')
            if 'pending_email' not in columns:db.execute('ALTER TABLE users ADD COLUMN pending_email TEXT COLLATE NOCASE')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email) WHERE email IS NOT NULL')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def transaction(self):
        db=self.connect()
        try:
            # Serializes account decisions, session checks and writes in this LOCAL store.
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
