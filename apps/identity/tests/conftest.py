"""Shared fixtures for identity tests.

jwt_client(user, ...) creates a real JWT session so IsAuthenticatedSession is satisfied.
This validates the full authentication stack, unlike force_authenticate() which bypasses it.
"""

import time

import pytest
from django.test import RequestFactory
from rest_framework.test import APIClient


@pytest.fixture
def jwt_client(db):
    """Factory: returns (client, session, raw_refresh) with a real Bearer token."""
    from apps.identity.services import session_service, token_service

    rf = RequestFactory()

    def _make(user, amr=None, tid=None, mid=None):
        req = rf.post("/")
        req.META["REMOTE_ADDR"] = "127.0.0.1"
        req.META["HTTP_USER_AGENT"] = "pytest"
        session, raw_refresh = session_service.create_session(user, req)
        access_token = token_service.issue_access_token(
            user,
            session.id,
            amr or ["pwd"],
            time.time(),
            tid=str(tid) if tid else None,
            mid=str(mid) if mid else None,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        return client, session, raw_refresh

    return _make
