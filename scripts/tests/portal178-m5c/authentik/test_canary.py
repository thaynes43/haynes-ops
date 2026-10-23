#!/usr/bin/env python3
"""Run only in the deployed digest's disposable, network-disabled image.

Actual decorated UserInfo and log processors; synthetic unsaved token models;
mocked token lookup/Event.new, blocked persistence. Print only safe proof metadata.
"""
import contextlib
from datetime import timedelta
import difflib
import hashlib
import importlib
import io
import json
import logging.config
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch

SOURCE_SHA = "131cfcb81374f9beccb7cad6830fbcc9917f030ea5d3e4cfb26b470db6d6ebcb"
phase = "setup"
results = []
captured = io.StringIO()


def run():
    global phase
    os.nice(19)
    # Authentik is image-owned source at /, not an installed wheel. Explicitly
    # add only that immutable image root under Python -I; no host imports.
    sys.path.insert(0, "/")
    import django
    django.setup()
    from django.db import connections
    from django.db.models import Model
    from django.test import RequestFactory
    from django.utils.timezone import now
    from authentik.core.models import User
    from authentik.events.models import Event
    from authentik.lib.logging import get_logger_config, structlog_configure
    from authentik.providers.oauth2 import utils
    from authentik.providers.oauth2.models import AccessToken, OAuth2Provider
    from authentik.providers.oauth2.views import userinfo

    # Any accidental query is a failed fixture, never a real DB operation.
    for connection in connections.all():
        connection.cursor = lambda *a, **k: (_ for _ in ()).throw(AssertionError("database access"))
    structlog_configure()
    logging.config.dictConfig(get_logger_config())
    original = Path(utils.__file__).read_text()
    assert hashlib.sha256(original.encode()).hexdigest() == SOURCE_SHA
    corrected = original
    for message, level in [("Token does not exist", "debug"), ("Token has expired", "debug"),
                           ("Revoked token was used", "warning")]:
        before = f'LOGGER.{level}("{message}", access_token=access_token)'
        assert corrected.count(before) == 1
        corrected = corrected.replace(before, f'LOGGER.{level}("{message}")')
    proposed = "".join(difflib.unified_diff(original.splitlines(keepends=True),
        corrected.splitlines(keepends=True), fromfile="a/authentik/providers/oauth2/utils.py",
        tofile="b/authentik/providers/oauth2/utils.py"))
    assert proposed == Path("/proposal/authentik-userinfo-log.patch").read_text()

    user = User(pk=101, username="synthetic-user", email="synthetic@example.invalid")
    provider = OAuth2Provider(pk=102, name="synthetic-provider")
    factory = RequestFactory()
    real_new = Event.new
    for fixed in [False, True]:
        phase = "corrected" if fixed else "baseline"
        if fixed:
            # Apply only the reviewed minimal proposal, in process memory; no
            # shared image/filesystem is changed. Rebuild the actual decorators.
            exec(compile(corrected, utils.__file__, "exec"), utils.__dict__)
            importlib.reload(userinfo)
        for shape, canary in [("opaque", "PORTAL178_SYNTHETIC_OPAQUE_NONCREDENTIAL"),
                              ("jwt-shaped", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJTWU5USEVUSUMifQ.SYNTHETIC")]:
            for branch in ["missing", "expired", "revoked"]:
                token = None if branch == "missing" else AccessToken(
                    token=canary, user=user, provider=provider,
                    expires=now() + timedelta(minutes=-1 if branch == "expired" else 1),
                    revoked=(branch == "revoked"))
                events = []
                def mocked_new(**kwargs):
                    # Still run real model cleansing + HTTP metadata serialization,
                    # with context processors disabled and Model.save mocked below.
                    event = real_new(app="authentik.providers.oauth2.utils", **kwargs)
                    events.append(event)
                    return event
                request = factory.get("/application/o/userinfo/", secure=True,
                    HTTP_HOST="authentik.haynesnetwork.com", HTTP_AUTHORIZATION="Bearer " + canary,
                    HTTP_USER_AGENT="portal-synthetic-canary")
                request.user = user
                captured.seek(0)
                captured.truncate(0)
                with patch.object(AccessToken.objects, "filter", return_value=SimpleNamespace(first=lambda: token)), \
                     patch.object(Event, "new", side_effect=mocked_new) as event_new, \
                     patch.object(Model, "save", autospec=True), \
                     patch("authentik.events.models.get_context_processors", return_value=[]), \
                     patch("authentik.events.models.ClientIPMiddleware.get_client_ip", return_value="127.0.0.1"):
                    response = userinfo.UserInfoView.as_view()(request)
                logs = captured.getvalue()
                expected_event = {"missing": "Token does not exist", "expired": "Token has expired",
                                  "revoked": "Revoked token was used"}[branch]
                assert expected_event in logs, "branch not observed in actual logger"
                visible = canary in logs
                event_visible = any(canary in json.dumps(e.context, default=str) for e in events)
                response_visible = canary in response.content.decode() or canary in str(dict(response.headers))
                assert response.status_code == 401
                assert visible is (not fixed)
                assert not event_visible and not response_visible
                assert event_new.called is (branch == "revoked")
                results.append({"phase": phase, "shape": shape, "branch": branch,
                    "status": response.status_code, "logCanaryVisible": visible,
                    "eventCanaryVisible": event_visible, "responseCanaryVisible": response_visible,
                    "eventPathExercised": event_new.called})


try:
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
        run()
except BaseException as error:
    # Do not print captured logs, canaries, exception values or environment.
    print(json.dumps({"phase": phase, "proof": False, "errorType": type(error).__name__,
                      "frames": [{"file": Path(f.filename).name, "line": f.lineno}
                                 for f in traceback.extract_tb(error.__traceback__)[-4:]]}))
    raise SystemExit(1)
print(json.dumps({"proof": True, "cases": results, "realTokens": False, "databaseWrites": False}))
