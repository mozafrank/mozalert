# mozalert

mozalert is a monitoring tool written in python which runs as a Kubernetes CustomResourceDefinition (CRD). mozalert maintains intervals for executing predefined container-based checks, and handles escalations if necessary.

## How to Install

```
git clone https://github.com/mozafrank/mozalert
cd mozalert/install
for f in clusterrole.yaml clusterrolebinding.yaml crd.yaml deploy.yaml; do
    kubectl apply -f $f
done
```

## How to Use

The mozalert CRD provides the "check" operator. Here is an example Check manifest:
```
kind: Check
metadata:
  name: check-test-1
  namespace: default
spec:
  check_interval: 1m
  retry_interval: 3m
  notification_interval: 5m
  max_attempts: 3
  escalations:
  - type: email
    args:
      email: you@email.com
  image: afrank/mozlenium
  secret_ref: check-test-1-mozlenium-secrets
  check_cm: check-test-1-cm
```
Available parameters:
* `check_interval`:
  *REQUIRED* Define the interval by which to run your check. Supports XXhXXmXXs format. An interval is defined as the amount of time _since the last check finished_ to wait before starting a new check.
* `retry_interval`:
  *OPTIONAL*: Define check interval to use while the check is in a failure state. For example, let's say you want your check to fail 3 times before it escalates, but you only want the check to run every 30 minutes. 3 failures would mean it'll take 90 minutes of downtime before the escalation is sent. Instead, you can set your `retry_interval` to, say, 3 minutes, then you'll get alerted much more quickly for a persistent failure.
* `notification_interval`:
  *OPTIONAL*: Define the check interval while the check is in an escalated state. This is also the interval for which alerts are sent out. If not specified this is set to `check_interval`.
* `max_attempts`:
  *REQUIRED*: The number of check attempts before a check enters a failed state and escalation begins.
* `escalations`: 
  *REQUIRED*: A List of escalations defined by a dictionary with keys `type` and `args`. Currently only supports email, but in the future this will be used for HTTP-based escalations as well. For an escalation of type email, one arg is required, with key "email".
* `image`:
  *REQUIRED*: Specify the image to be used by the checker. [Example Images](https://github.com/mozafrank/mozalert/tree/master/checkers)
* `secret_ref`:
  *OPTIONAL*: The name of a secret resource to use for this check. Secrets defined here show up in the container env (or `$secure` in the case of mozlenium).
* `check_cm`:
  *OPTIONAL*: The ConfigMap which holds your actual check code. See example below.
* `url`:
  *OPTIONAL*: URL to check for url-based checks.
* `template.spec`: 
  *OPTIONAL* Instead of specifying image, secret_ref and check_cm you can override everything by defining a full pod spec which will get used by the checker. You can see examples of this [here](https://github.com/mozafrank/mozalert/blob/master/examples/test-1-with-cm.yaml) and [here](https://github.com/mozafrank/mozalert/blob/master/examples/test-1-with-secret.yaml).
* `timeout`:
  *OPTIONAL* Max time for check to run before being killed. Default 5m.

Example secret manifest for a check:
```
# here is where you put any secrets you want your
# check to have. The secret value is base64-encoded
# For example: echo -n thisisnotsecret | base64 -w0
kind: Secret
type: Opaque
apiVersion: v1
metadata:
  name: check-test-1-mozlenium-secrets
  namespace: default
data:
  SECRETSTUFF: dGhpc2lzbm90c2VjcmV0
```

Example ConfigMap for a mozlenium check:
```
# here is the check itself, stored in a configmap
# this block was generated with
# k create configmap check-test-1-cm --from-file=./demo-check.js
kind: ConfigMap
apiVersion: v1
metadata:
  name: check-test-1-cm
  namespace: default
data:
  demo-check.js: |+
    //demo check
    require('mozlenium')();
    var assert = require('assert');
    var url = 'https://www.google.com'
    console.log("starting check");
    $browser.get(url);
    console.log($secure.SECRETSTUFF);
    console.log("well that went great");
```

Interacting with the operator:
```
$ kubectl get checks
NAME              STATUS   STATE   ATTEMPT   MAX_ATTEMPTS   ESCALATION                       LAST_CHECK            NEXT_CHECK            AGE
check-test-1      OK       IDLE    0         3              afrank@mozilla.com               2020-06-16 14:07:55   2020-06-16 14:08:55   2d20h
```

Getting detailed output from a check, including logs of the last run:
```
$ kubectl get check check-test-1 -oyaml
apiVersion: crd.k8s.afrank.local/v1
kind: Check
metadata:
  name: check-test-1
  namespace: default
spec:
  check_cm: check-test-1-cm
  check_interval: 1m
  escalations:
  - type: email
    args:
      email: afrank@mozilla.com
  image: afrank/mozlenium
  max_attempts: 3
  notification_interval: 10m
  retry_interval: 1m
  secret_ref: check-test-1-mozlenium-secrets
status:
  attempt: "0"
  lastCheckTimestamp: "2020-06-16 14:11:14"
  logs: |
    //demo check
    starting check
    thisisnotsecret
    well that went great
    Check finished in 2 seconds with status code 0
  nextCheckTimestamp: "2020-06-16 14:12:14"
  state: IDLE
  status: OK
```

## How to Develop

The entire stack is meant to run in Kubernetes but for development can be run locally or via docker.

### Running locally

```
pip3 install .
mozalert
```

```
docker build -t mozalert-controller .
```
