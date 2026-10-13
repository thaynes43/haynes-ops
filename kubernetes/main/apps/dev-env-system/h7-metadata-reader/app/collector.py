"""One finite, read-only H7 audit. Never log an API body or exception text."""

import copy
import datetime
import hashlib
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NAMESPACE = "dev-agents"
API = "https://10.43.0.1:443"
ARCHIVES = {"dev-env-history-archive", "dev-env-coordination-archive"}
KINDS = (
    ("v1", "pods", "Pod"),
    ("v1", "replicationcontrollers", "ReplicationController"),
    ("apps/v1", "deployments", "Deployment"),
    ("apps/v1", "replicasets", "ReplicaSet"),
    ("apps/v1", "statefulsets", "StatefulSet"),
    ("apps/v1", "daemonsets", "DaemonSet"),
    ("batch/v1", "jobs", "Job"),
    ("batch/v1", "cronjobs", "CronJob"),
    ("apps/v1", "controllerrevisions", "ControllerRevision"),
)
PAGE_SIZE = 32
MAX_PAGES = 4
MAX_OBJECTS = 256
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REQUESTS = 64
MAX_SECONDS = 120
NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}\Z")
UID = re.compile(r"[a-zA-Z0-9-]{1,128}\Z")


class Refused(Exception):
    def __init__(self, code, http_status=None):
        self.code = code
        self.http_status = http_status


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def text_digest(value):
    require(isinstance(value, str), "MalformedStatusText")
    return {"sha256": hashlib.sha256(value.encode()).hexdigest(), "bytes": len(value.encode())}


def identity(value, api_version, kind, namespace=None, name=None):
    require(isinstance(value, dict), "MalformedObject")
    require(value.get("apiVersion") == api_version and value.get("kind") == kind, "WrongObjectKind")
    meta = value.get("metadata", {})
    require(isinstance(meta, dict), "MalformedMetadata")
    require(isinstance(meta.get("name"), str) and NAME.fullmatch(meta["name"]), "MalformedObjectName")
    require(isinstance(meta.get("uid"), str) and UID.fullmatch(meta["uid"]), "MissingObjectUID")
    require(isinstance(meta.get("resourceVersion"), str) and re.fullmatch(r"[0-9]{1,32}", meta["resourceVersion"]), "MissingResourceVersion")
    require(namespace is None or meta.get("namespace") == namespace, "WrongObjectNamespace")
    require(name is None or meta["name"] == name, "WrongObjectName")
    return {"apiVersion": api_version, "kind": kind, "name": meta["name"], "uid": meta["uid"],
            "resourceVersion": meta["resourceVersion"], **({"namespace": namespace} if namespace else {})}


def api_path(version, resource, namespace=None, name=None):
    base = "/api/" + version if "/" not in version else "/apis/" + version
    if namespace:
        base += "/namespaces/" + namespace
    return base + "/" + resource + ("/" + name if name else "")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("RedirectRefused", code)


class Reader:
    def __init__(self):
        # No kubeconfig, proxies, provider HOME, SDK, credential discovery or subprocess.
        self.started = time.monotonic()
        self.requests = 0
        self.bytes = 0
        self.token = pathlib.Path("/var/run/h7-api/token").read_text().strip()
        require(bool(self.token), "MissingOwnCredential")
        context = ssl.create_default_context(cafile="/var/run/h7-api/ca.crt")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPSHandler(context=context))

    def get(self, path, query=None):
        require(path.startswith(("/api/v1/", "/apis/apps/v1/", "/apis/batch/v1/",
                                 "/apis/policies.kyverno.io/v1/", "/apis/admissionregistration.k8s.io/v1/"))
                and ".." not in path, "UnexpectedRequestPath")
        require(self.requests < MAX_REQUESTS and time.monotonic() - self.started < MAX_SECONDS, "RequestBudgetExhausted")
        self.requests += 1
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token,
                                                     "Accept": "application/json"}, method="GET")
        try:
            with self.opener.open(request, timeout=min(5, MAX_SECONDS - (time.monotonic() - self.started))) as response:
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            # Do not read the body, stringify the error, or expose response headers.
            raise Refused("HTTPReadRefused", error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Refused("TransportReadRefused") from None
        self.bytes += len(data)
        require(len(data) <= MAX_RESPONSE_BYTES and self.bytes <= MAX_TOTAL_BYTES, "ResponseBudgetExhausted")
        try:
            return json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise Refused("MalformedJSONResponse") from None


def complete_list(reader, version, resource, kind):
    items, seen, tokens, rv = [], set(), set(), None
    continuation = None
    started = timestamp()
    for page in range(MAX_PAGES):
        query = {"limit": PAGE_SIZE}
        if continuation:
            query["continue"] = continuation
        response = reader.get(api_path(version, resource, NAMESPACE), query)
        require(isinstance(response, dict) and response.get("apiVersion") == version
                and response.get("kind") == kind + "List", "MalformedList")
        meta = response.get("metadata", {})
        require(isinstance(meta, dict), "MalformedListMetadata")
        current_rv = meta.get("resourceVersion")
        require(isinstance(current_rv, str) and re.fullmatch(r"[0-9]{1,32}", current_rv), "MissingListResourceVersion")
        require(rv is None or rv == current_rv, "ChangingPaginationResourceVersion")
        rv = current_rv
        rows = response.get("items")
        require(isinstance(rows, list) and len(rows) <= PAGE_SIZE, "MalformedListItems")
        for row in rows:
            fact = identity(row, version, kind, NAMESPACE)
            key = (fact["name"], fact["uid"])
            require(key not in seen and all(previous[0] != key[0] for previous in seen), "DuplicateListObject")
            seen.add(key)
            items.append(row)
        require(len(items) <= PAGE_SIZE * MAX_PAGES, "ObjectBudgetExhausted")
        continuation = meta.get("continue", "")
        require(isinstance(continuation, str) and len(continuation) <= 8192, "MalformedContinuation")
        remaining = meta.get("remainingItemCount")
        require(remaining is None or (type(remaining) is int and remaining >= 0), "MalformedRemainingCount")
        if not continuation:
            require(remaining in (None, 0), "IncompletePagination")
            return items, {"kind": kind, "resourceVersion": rv, "count": len(items), "pages": page + 1,
                           "complete": True, "startedAt": started, "finishedAt": timestamp()}
        require(bool(rows) and continuation not in tokens and remaining != 0, "IncoherentPagination")
        tokens.add(continuation)
    raise Refused("PageBudgetExhausted")


def normalized_policy(spec, kind):
    require(isinstance(spec, dict), "MissingPolicySpec")
    result = copy.deepcopy(spec)
    if kind == "ValidatingPolicy":
        evaluation = result.setdefault("evaluation", {})
        evaluation.setdefault("admission", {}).setdefault("enabled", True)
        evaluation.setdefault("background", {}).setdefault("enabled", True)
    if kind != "ValidatingAdmissionPolicyBinding":
        match = result.setdefault("matchConstraints", {})
        match.setdefault("matchPolicy", "Equivalent")
        match.setdefault("namespaceSelector", {})
        match.setdefault("objectSelector", {})
        for rule in match.get("resourceRules", []):
            rule.setdefault("scope", "*")
    return result


def readiness(obj):
    status = obj.get("status", {})
    require(isinstance(status, dict), "MalformedPolicyStatus")
    if obj["kind"] == "ValidatingPolicy":
        state = status.get("conditionStatus", {})
        require(isinstance(state, dict), "MissingKyvernoConditionStatus")
        conditions = state.get("conditions", [])
        require(isinstance(conditions, list) and len(conditions) <= 16, "MalformedPolicyConditions")
        safe = []
        types = set()
        for condition in conditions:
            require(isinstance(condition, dict), "MalformedPolicyCondition")
            kind = condition.get("type")
            require(kind in {"WebhookConfigured", "PolicyCached", "RBACPermissionsGranted"} and kind not in types,
                    "UnexpectedPolicyCondition")
            types.add(kind)
            generation = condition.get("observedGeneration", 0)
            require(type(generation) is int and generation >= 0, "MalformedConditionGeneration")
            safe.append({"type": kind, "status": condition.get("status") if condition.get("status") in
                         ("True", "False", "Unknown") else "Unknown", "reason": condition.get("reason") if
                         condition.get("reason") in ("Succeeded", "Failed") else "Unknown",
                         "message": text_digest(condition.get("message", "")),
                         "observedGeneration": generation})
        result = {"ready": state.get("ready") is True, "conditions": safe,
                  "message": text_digest(state.get("message", "")), "generationAttested": False}
        good = result["ready"] and types == {"WebhookConfigured", "PolicyCached", "RBACPermissionsGranted"}
        good = good and all(c["status"] == "True" for c in safe) and state.get("message", "") == ""
        return result, good
    if obj["kind"] == "ValidatingAdmissionPolicy":
        warnings = status.get("typeChecking", {}).get("expressionWarnings", [])
        require(isinstance(warnings, list) and len(warnings) <= 64, "MalformedTypeChecking")
        generation = obj["metadata"].get("generation")
        observed = status.get("observedGeneration")
        require(type(generation) is int and generation >= 0 and
                (observed is None or (type(observed) is int and observed >= 0)), "MalformedPolicyGeneration")
        return {"observedGeneration": observed, "generation": generation,
                "expressionWarningCount": len(warnings), "expressionWarningsSha256": digest(warnings)}, \
            observed == generation and not warnings
    return {"binding": True}, True


def normalize_conditions(conditions):
    require(isinstance(conditions, list), "MalformedMatchConditions")
    # Only whitespace outside CEL string literals is ignored; literals remain exact.
    def expression(value):
        require(isinstance(value, str), "MalformedMatchExpression")
        tokens = re.findall(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|&&|\|\||==|!=|<=|>=|\S", value)
        return tuple(tokens)
    return sorted((c["name"], expression(c["expression"])) for c in conditions)


def normalize_rules(rules):
    require(isinstance(rules, list), "MalformedWebhookRules")
    for rule in rules:
        require(isinstance(rule, dict) and set(rule) <= {"apiGroups", "apiVersions", "operations", "resources", "scope"}, "MalformedWebhookRule")
        for key in ("apiGroups", "apiVersions", "operations", "resources"):
            require(isinstance(rule.get(key), list) and all(isinstance(value, str) for value in rule[key]), "MalformedWebhookRuleValues")
    return sorted(canonical({key: sorted(r.get(key, [])) for key in
                             ("apiGroups", "apiVersions", "operations", "resources")} | {"scope": r.get("scope", "*")})
                  for r in rules)


def selector_matches(selector, labels):
    require(isinstance(selector, dict) and set(selector) <= {"matchLabels", "matchExpressions"}, "MalformedNamespaceSelector")
    for key, value in selector.get("matchLabels", {}).items():
        if labels.get(key) != value:
            return False
    for rule in selector.get("matchExpressions", []):
        require(set(rule) <= {"key", "operator", "values"}, "MalformedSelectorRequirement")
        key, op, values = rule.get("key"), rule.get("operator"), rule.get("values", [])
        require(isinstance(key, str) and isinstance(values, list), "MalformedSelectorRequirement")
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and key in labels and labels[key] in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
        require(op in {"In", "NotIn", "Exists", "DoesNotExist"}, "UnknownSelectorOperator")
    return True


def verify_hook(config, expected, labels):
    name = expected["metadata"]["name"]
    hooks = config.get("webhooks", [])
    require(isinstance(hooks, list) and len(hooks) <= 256, "MalformedWebhooks")
    selected = [hook for hook in hooks if hook.get("clientConfig", {}).get("service", {}).get("path") == "/vpol/" + name]
    require(len(selected) == 1, "MissingOrDuplicatePolicyHook")
    hook = selected[0]
    require(hook.get("name") == "vpol.validate.kyverno.svc-fail-finegrained-" + name, "WrongPolicyHookName")
    service = hook.get("clientConfig", {}).get("service")
    require(service == {"name": "kyverno-svc", "namespace": "kyverno", "path": "/vpol/" + name, "port": 443}, "WrongPolicyHookService")
    require(hook.get("failurePolicy") == "Fail" and hook.get("timeoutSeconds") == 10, "WrongWebhookFailureSettings")
    require(hook.get("sideEffects") == "NoneOnDryRun" and hook.get("matchPolicy") == "Equivalent"
            and hook.get("admissionReviewVersions") == ["v1"], "WrongWebhookProtocol")
    require(bool(hook.get("clientConfig", {}).get("caBundle")) and "url" not in hook.get("clientConfig", {}), "MissingWebhookCA")
    require(normalize_rules(hook.get("rules")) == normalize_rules(expected["spec"]["matchConstraints"]["resourceRules"]), "WrongWebhookRules")
    require(normalize_conditions(hook.get("matchConditions", [])) == normalize_conditions(expected["spec"].get("matchConditions", [])), "WrongWebhookConditions")
    selector = hook.get("namespaceSelector", {})
    require(selector_matches(selector, labels) and hook.get("objectSelector", {}) == {}, "WebhookExcludesTarget")
    for key, value in expected["spec"]["matchConstraints"].get("namespaceSelector", {}).get("matchLabels", {}).items():
        require(selector.get("matchLabels", {}).get(key) == value, "MissingWebhookNamespaceBoundary")
    # All emitted expressions are the verified committed public source, never arbitrary live strings.
    return {"policy": name, "name": hook["name"], "service": service, "rules": hook["rules"],
            "failurePolicy": "Fail", "timeoutSeconds": 10, "sideEffects": "NoneOnDryRun", "matchPolicy": "Equivalent",
            "admissionReviewVersions": ["v1"], "namespaceSelector": selector, "objectSelector": {},
            "matchConditions": expected["spec"].get("matchConditions", []), "caBundlePresent": True}


def template(obj):
    kind = obj["kind"]
    if kind == "Pod":
        return {"metadata": obj["metadata"], "spec": obj.get("spec")}
    if kind == "ControllerRevision":
        data = obj.get("data")
        require(isinstance(data, dict), "MalformedRevisionData")
        return data.get("spec", {}).get("template")
    spec = obj.get("spec")
    require(isinstance(spec, dict), "MalformedWorkloadSpec")
    if kind == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template")


def workload_facts(obj):
    t = template(obj)
    require(isinstance(t, dict) and isinstance(t.get("spec"), dict), "MissingPodSpec")
    spec = t["spec"]
    require(isinstance(spec.get("containers"), list) and bool(spec["containers"]), "MissingContainers")
    volumes = spec.get("volumes", [])
    require(isinstance(volumes, list) and len(volumes) <= 256, "MalformedVolumes")
    claims = []
    for volume in volumes:
        require(isinstance(volume, dict), "MalformedVolume")
        if "persistentVolumeClaim" in volume:
            pvc = volume["persistentVolumeClaim"]
            require(isinstance(pvc, dict) and isinstance(pvc.get("claimName"), str)
                    and NAME.fullmatch(pvc["claimName"]), "MalformedClaim")
            require(isinstance(volume.get("name"), str) and NAME.fullmatch(volume["name"])
                    and type(pvc.get("readOnly", False)) is bool, "MalformedClaimVolume")
            claims.append({"volume": volume["name"], "claim": pvc["claimName"], "readOnly": pvc.get("readOnly", False)})
    sa = spec.get("serviceAccountName", "default") or "default"
    require(isinstance(sa, str) and NAME.fullmatch(sa), "MalformedServiceAccount")
    meta = obj["metadata"]
    owners = meta.get("ownerReferences", [])
    require(isinstance(owners, list) and len(owners) <= 16, "MalformedOwners")
    safe_owners = []
    for owner in owners:
        require(isinstance(owner, dict) and isinstance(owner.get("name"), str) and NAME.fullmatch(owner["name"])
                and isinstance(owner.get("uid"), str) and UID.fullmatch(owner["uid"]), "MalformedOwnerIdentity")
        require(isinstance(owner.get("kind"), str) and re.fullmatch(r"[A-Z][a-zA-Z0-9]{0,63}", owner["kind"])
                and isinstance(owner.get("apiVersion"), str) and re.fullmatch(r"(?:[a-z0-9.-]+/)?v[0-9]+(?:alpha[0-9]+|beta[0-9]+)?", owner["apiVersion"]), "MalformedOwnerKind")
        safe_owners.append({k: owner[k] for k in ("apiVersion", "kind", "name", "uid", "controller", "blockOwnerDeletion") if k in owner})
    fact = identity(obj, obj["apiVersion"], obj["kind"], NAMESPACE)
    fact.update({"owners": safe_owners, "claims": claims, "serviceAccount": sa,
                 "templateSha256": digest(t), "podSpecSha256": digest(spec)})
    return fact


def protected(obj, fact):
    meta = obj["metadata"]
    template_meta = template(obj).get("metadata", {})
    require(isinstance(template_meta, dict), "MalformedTemplateMetadata")
    def marker(value):
        return value.get("labels", {}).get("app.kubernetes.io/name") == "dev-env-shelf" or any(
            (o.get("kind") == "Deployment" and o.get("name") == "dev-env-shelf") or
            (o.get("kind") == "ReplicaSet" and o.get("name", "").startswith("dev-env-shelf-"))
            for o in value.get("ownerReferences", []))
    return bool(ARCHIVES.intersection(c["claim"] for c in fact["claims"])) or \
        fact["serviceAccount"] == "dev-env-shelf" or marker(meta) or marker(template_meta) or \
        (obj["kind"] == "Deployment" and meta["name"] == "dev-env-shelf") or \
        (obj["kind"] in {"Pod", "ReplicaSet"} and (meta["name"].startswith("dev-env-shelf-") or
                                                 meta.get("generateName", "").startswith("dev-env-shelf-")))


def normalize_template(value, replica=False, pod=False, expected_pod=None):
    require(isinstance(value, dict), "MalformedTemplate")
    result = copy.deepcopy(value)
    meta = result.get("metadata", {})
    require(isinstance(meta, dict), "MalformedTemplateMetadata")
    if pod:
        # Ignore only concrete API bookkeeping and the observed CNI/topology metadata.
        meta = {k: v for k, v in meta.items() if k in {"labels", "annotations"}}
        labels = meta.setdefault("labels", {})
        for key, expected in {"topology.kubernetes.io/region": "main", "topology.kubernetes.io/zone": "w"}.items():
            if key in labels:
                require(labels.pop(key) == expected, "UnexpectedPodTopology")
        annotations = meta.get("annotations", {})
        require(set(annotations) <= {"k8s.v1.cni.cncf.io/network-status"}, "UnexpectedPodAnnotation")
        meta.pop("annotations", None)
    meta.pop("creationTimestamp", None)  # server renders null for template metadata
    if replica or pod:
        meta.get("labels", {}).pop("pod-template-hash", None)
    result["metadata"] = meta
    spec = result.get("spec", {})
    require(isinstance(spec, dict), "MalformedPodSpec")
    for key, default in {"dnsPolicy": "ClusterFirst", "restartPolicy": "Always", "schedulerName": "default-scheduler",
                         "terminationGracePeriodSeconds": 30}.items():
        if spec.get(key) == default:
            spec.pop(key)
    if spec.get("serviceAccount") == spec.get("serviceAccountName"):
        spec.pop("serviceAccount", None)
    if pod:
        require(expected_pod is not None and spec.get("nodeName") == expected_pod["nodeName"], "UnexpectedPodPlacement")
        spec.pop("nodeName")
        for key, default in {"priority": 0, "preemptionPolicy": "PreemptLowerPriority"}.items():
            if spec.get(key) == default:
                spec.pop(key)
        tolerations = spec.pop("tolerations", [])
        allowed = [{"key": key, "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 300}
                   for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")]
        require(sorted(map(canonical, tolerations)) == sorted(map(canonical, allowed)), "UnexpectedPodTolerations")
    for container in spec.get("containers", []):
        for key, default in {"imagePullPolicy": "IfNotPresent", "terminationMessagePath": "/dev/termination-log",
                             "terminationMessagePolicy": "File"}.items():
            if container.get(key) == default:
                container.pop(key)
    return result


def verify_baseline(objects, expected):
    by_key = {(o["kind"], o["metadata"]["name"]): o for o in objects}
    require(len(by_key) == len(objects), "DuplicateBaselineObject")
    protected_rows, facts = [], []
    root = by_key.get(("Deployment", "dev-env-shelf"))
    require(root is not None, "MissingShelfRoot")
    allowed = expected["lineage"]
    for obj in objects:
        fact = workload_facts(obj)
        facts.append(fact)
        if not protected(obj, fact):
            continue
        require(not ARCHIVES.intersection(c["claim"] for c in fact["claims"]), "UnexpectedArchiveBearingObject")
        key = obj["kind"] + "/" + obj["metadata"]["name"]
        require(key in allowed, "UnexpectedShelfObject")
        approved = allowed[key]
        require(obj["metadata"]["uid"] == approved["uid"], "ChangedShelfUID")
        require(fact["serviceAccount"] == "dev-env-shelf" and obj["metadata"].get("labels", {}).get("app.kubernetes.io/name") == "dev-env-shelf", "WrongShelfIdentity")
        controls = [owner for owner in fact["owners"] if owner.get("controller") is True]
        if obj["kind"] == "Deployment":
            require(not fact["owners"] and obj["metadata"].get("labels", {}).get("kustomize.toolkit.fluxcd.io/name") == "dev-env-shelf"
                    and obj["metadata"].get("labels", {}).get("kustomize.toolkit.fluxcd.io/namespace") == "dev-env-system", "WrongShelfSourceOwnership")
        else:
            require(len(controls) == 1 and len(fact["owners"]) == 1, "AmbiguousShelfParent")
            owner = controls[0]
            parent = by_key.get((owner.get("kind"), owner.get("name")))
            require(parent is not None and parent["apiVersion"] == owner.get("apiVersion")
                    and parent["metadata"]["namespace"] == NAMESPACE and parent["metadata"]["uid"] == owner["uid"], "StaleShelfParent")
            require(owner["kind"] + "/" + owner["name"] == approved["parent"], "ReparentedShelfObject")
        value = normalize_template(template(obj), replica=obj["kind"] == "ReplicaSet", pod=obj["kind"] == "Pod", expected_pod=approved)
        source = expected["shelfTemplates"][approved["sourceCommit"]]
        require(value == normalize_template(source), "ShelfTemplateSourceMismatch")
        fact.update({"sourceCommit": approved["sourceCommit"], "normalizedTemplateSha256": digest(value)})
        protected_rows.append(fact)
    require({row["kind"] + "/" + row["name"] for row in protected_rows} == set(allowed), "IncompleteShelfLineage")
    return sorted(protected_rows, key=lambda row: (row["kind"], row["name"])), digest(facts)


def audit(reader, expected, report):
    policy_objects = []
    for source in expected["policies"]:
        name, version, kind, resource = source["metadata"]["name"], source["apiVersion"], source["kind"], source["resource"]
        obj = reader.get(api_path(version, resource, name=name))
        fact = identity(obj, version, kind, name=name)
        fact["specSha256"] = digest(obj.get("spec"))
        normalized = normalized_policy(obj.get("spec"), kind)
        same = normalized == normalized_policy(source["spec"], kind)
        state, ready = readiness(obj)
        fact.update({"matchesCommittedSource": same, "readiness": state})
        report["policies"].append(fact)
        require(same, "InstalledPolicySourceMismatch")
        require(ready, "InstalledPolicyNotReady")
        # Report only the verified public policy configuration, not arbitrary live spec.
        fact["configuration"] = {k: source["spec"][k] for k in ("failurePolicy", "validationActions", "policyName",
                                  "matchConstraints", "matchConditions", "evaluation", "autogen") if k in source["spec"]}
        policy_objects.append(obj)
    namespace = reader.get(api_path("v1", "namespaces", name=NAMESPACE))
    report["namespace"] = identity(namespace, "v1", "Namespace", name=NAMESPACE)
    labels = namespace["metadata"].get("labels", {})
    require(labels.get("kubernetes.io/metadata.name") == NAMESPACE, "WrongNamespaceSelectionLabel")
    report["namespace"]["selectionLabel"] = {"kubernetes.io/metadata.name": NAMESPACE}
    config = reader.get(api_path("admissionregistration.k8s.io/v1", "validatingwebhookconfigurations", name="kyverno-resource-validating-webhook-cfg"))
    report["webhookConfiguration"] = identity(config, "admissionregistration.k8s.io/v1", "ValidatingWebhookConfiguration", name="kyverno-resource-validating-webhook-cfg")
    for source in expected["policies"]:
        if source["kind"] == "ValidatingPolicy":
            report["hooks"].append(verify_hook(config, source, labels))
    objects = []
    for version, resource, kind in KINDS:
        rows, summary = complete_list(reader, version, resource, kind)
        objects.extend(rows)
        report["lists"].append(summary)
        require(len(objects) <= MAX_OBJECTS, "TotalObjectBudgetExhausted")
    report["protectedObjects"], report["baselineFactsSha256"] = verify_baseline(objects, expected)
    # Pin exact named objects through the collection window. No retry or new snapshot.
    checks = policy_objects + [namespace, config]
    checks += [obj for obj in objects if any(obj["metadata"]["uid"] == row["uid"] for row in report["protectedObjects"])]
    resources = {kind: (version, resource) for version, resource, kind in KINDS}
    resources.update({obj["kind"] + "/" + obj["metadata"]["name"]: (obj["apiVersion"], source["resource"])
                      for obj, source in zip(policy_objects, expected["policies"])})
    for old in checks:
        kind, name = old["kind"], old["metadata"]["name"]
        if kind == "Namespace":
            version, resource = "v1", "namespaces"
        elif kind == "ValidatingWebhookConfiguration":
            version, resource = "admissionregistration.k8s.io/v1", "validatingwebhookconfigurations"
        else:
            version, resource = resources.get(kind + "/" + name, resources.get(kind))
        fresh = reader.get(api_path(version, resource, old["metadata"].get("namespace"), name))
        require(identity(fresh, version, kind, old["metadata"].get("namespace"), name) ==
                identity(old, version, kind, old["metadata"].get("namespace"), name), "NamedObjectChangedDuringAudit")
    report["namedObjectsStable"] = True
    report["state"] = "Ready"


def main():
    report = {"schemaVersion": 1, "state": "Failed", "purpose": "H7InstalledConfigurationAndPremountBaseline",
              "modelFree": True, "startedAt": timestamp(), "policies": [], "hooks": [], "lists": [],
              "protectedObjects": [], "limits": {"pagesPerKind": MAX_PAGES, "pageSize": PAGE_SIZE,
              "totalObjects": MAX_OBJECTS, "requests": MAX_REQUESTS, "responseBytes": MAX_RESPONSE_BYTES,
              "totalResponseBytes": MAX_TOTAL_BYTES, "seconds": MAX_SECONDS},
              "limitations": ["Metadata observation only; no admission request outcomes tested.",
              "Per-kind pagination snapshots, not an atomic snapshot across different resource kinds.",
              "Protected named objects and installed configuration rechecked through this window; later trusted writes remain possible.",
              "Kyverno condition summaries do not attest observedGeneration in pinned1.19.1.",
              "No history mount, retention, persistence/restore, measurements or activation acceptance."]}
    reader = None
    try:
        expected = json.loads(pathlib.Path("/audit/expected.json").read_text())
        report["collectorSha256"] = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
        report["pythonVersion"] = list(sys.version_info[:3])
        report["expectedBundleSha256"] = digest(expected)
        report["sourceRevision"] = expected["sourceRevision"]
        reader = Reader()
        audit(reader, expected, report)
    except Refused as error:
        report["failure"] = {"code": error.code}
        if error.http_status is not None:
            report["failure"]["httpStatus"] = error.http_status
    except Exception:
        # Never let a traceback disclose inline data, an HTTP body, or a credential.
        report["failure"] = {"code": "MalformedOrUnexpectedInput"}
    report["finishedAt"] = timestamp()
    if reader:
        report["requests"] = reader.requests
        report["responseBytes"] = reader.bytes
    print(json.dumps(report, sort_keys=True, allow_nan=False), flush=True)
    return 0 if report["state"] == "Ready" else 1


if __name__ == "__main__":
    sys.exit(main())
