import os
import sys
from kubernetes import client, config, watch
import logging
from time import sleep

from types import SimpleNamespace
import datetime
import pytz

from mozalert.state import State, Status
from mozalert.base import BaseCheck
from mozalert.utils.sendgrid import SendGridTools

# kubernetes.client.rest.ApiException


class Check(BaseCheck):
    """
    the Check object handles the entire lifecycle of a check:
    * maintains the check interval using threading.Timer (BaseCheck)
    * manages the resources for running the check itself
    * reports status to the CRD object
    * handles escalation
    """

    def __init__(self, **kwargs):
        self.client = kwargs.get("client", client.BatchV1Api())
        self.pod_client = kwargs.get("pod_client", client.CoreV1Api())
        self.crd_client = kwargs.get("crd_client", client.CustomObjectsApi())

        super().__init__(**kwargs)

        default_escalation_template = """
        <p>
        <b>Name:</b> {namespace}/{name}<br>
        <b>Status:</b> {status}<br>                            
        <b>Attempt:</b> {attempt}/{max_attempts}<br>
        <b>Last Check:</b> {last_check}<br>
        <b>More Details:</b><br> <pre>{logs}</pre><br>
        </p>
        """

        self._config.spec = kwargs.get("spec", {})
        self._config.escalation_template = kwargs.get(
            "escalation_template", default_escalation_template
        )

    def escalate(self):
        logging.info(f"Escalating {self._config.namespace}/{self._config.name}")
        sendgrid_key = os.environ.get("SENDGRID_API_KEY", "")
        message = self._config.escalation_template.format(
            name=self._config.name,
            namespace=self._config.namespace,
            status=self._status.status.name,
            attempt=self._status.attempt,
            max_attempts=self._config.max_attempts,
            last_check=str(self._status.last_check),
            logs=self._status.logs,
        )
        SendGridTools.send_message(
            api_key=sendgrid_key,
            to_emails=[self._config.escalation],
            message=message,
            subject=f"Mozalert {self._status.status.name}: {self._config.namespace}/{self._config.name}",
        )
        logging.info(f"Message sent to {self._config.escalation}")

    def run_job(self):
        """
        Build the k8s resources, apply them, then poll for completion, and
        report status back to the thread.
        
        The k8s resources take the form:
            pod spec -> pod template -> job spec -> job

        """
        logging.info(f"Running job for {self._config.namespace}/{self._config.name}")
        pod_spec = client.V1PodSpec(**self._config.spec)
        template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": self._config.name}),
            spec=pod_spec,
        )
        job_spec = client.V1JobSpec(template=template, backoff_limit=0)
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=self._config.name),
            spec=job_spec,
        )
        try:
            res = self.client.create_namespaced_job(
                body=job, namespace=self._config.namespace
            )
            logging.info(
                f"Job created for {self._config.namespace}/{self._config.name}"
            )
        except Exception as e:
            logging.info(sys.exc_info()[0])
            logging.info(e)
            raise

        self._status.state = State.RUNNING
        self.set_crd_status()

        # wait for the job to finish
        while True:
            status = self.get_job_status()
            if status.active and self._status.state != State.RUNNING:
                self._status.state = State.RUNNING
                logging.info("Setting the state to RUNNING")
            if status.start_time:
                self._runtime = datetime.datetime.utcnow() - status.start_time.replace(
                    tzinfo=None
                )
            if status.succeeded:
                logging.info("Setting the job status to OK")
                self._status.status = Status.OK
                self._status.state = State.IDLE
            elif status.failed:
                logging.info("Setting the job status to CRITICAL")
                self._status.status = Status.CRITICAL
                self._status.state = State.IDLE

            if self._status.status != Status.PENDING and self._status.state != State.RUNNING:
                self.get_job_logs()
                for log_line in self._status.logs.split("\n"):
                    logging.debug(log_line)
                break
            sleep(self._job_poll_interval)
        logging.info(
            f"Job finished for {self._config.namespace}/{self._config.name} in {self._runtime.seconds} seconds with status {self._status.status}"
        )
        self._status.state = State.IDLE
        self._status.last_check = pytz.utc.localize(datetime.datetime.utcnow())
        self.set_crd_status()

    def get_job_logs(self):
        """
        since the CRD deletes the pod after its done running, it is nice
        to have a way to save the logs before deleting it. this retrieves
        the pod logs so they can be blasted into the controller logs.
        """
        res = self.pod_client.list_namespaced_pod(
            namespace=self._config.namespace, label_selector=f"app={self._config.name}"
        )
        logs = ""
        for pod in res.items:
            logs += self.pod_client.read_namespaced_pod_log(
                pod.metadata.name, self._config.namespace
            )
        self._status.logs = logs

    def get_job_status(self):
        """
        read the status of the job object and return a SimpleNamespace
        """

        status = SimpleNamespace(
            active=False, succeeded=False, failed=False, start_time=None
        )

        try:
            res = self.client.read_namespaced_job_status(
                self._config.name, self._config.namespace
            )
        except Exception as e:
            logging.info(sys.exc_info()[0])
            logging.info(e)
            return status

        if res.status.active == 1:
            status.active = True

        if res.status.succeeded:
            status.succeeded = True

        if res.status.failed:
            status.failed = True

        if res.status.start_time:
            status.start_time = res.status.start_time

        return status

    def set_crd_status(self):
        """
        Patch the status subresource of the check object in k8s to use the latest
        status. NOTE: In what I've read in the docs, this should NOT cause a modify
        event, however it does, even when hitting the apiserver directly. We are careful
        to account for this but TODO to understand this further.
        """
        logging.debug(
            f"Setting CRD status for {self._config.namespace}/{self._config.name}"
        )

        status = {
            "status": {
                "status": str(self._status.status.name),
                "state": str(self._status.state.name),
                "attempt": str(self._status.attempt),
                "lastCheckTimestamp": str(self._status.last_check).split(".")[0],
                "nextCheckTimestamp": str(self._status.next_check).split(".")[0],
                "logs": self._status.logs,
            }
        }

        try:
            res = self.crd_client.patch_namespaced_custom_object_status(
                "crd.k8s.afrank.local",
                "v1",
                self._config.namespace,
                "checks",
                self._config.name,
                body=status,
            )
        except Exception as e:
            # failed to set the status
            # TODO should take more action here
            logging.info(sys.exc_info()[0])
            logging.info(e)

    def delete_job(self):
        """
        after a check is complete delete the job which executed it
        """
        try:
            res = self.client.delete_namespaced_job(
                self._config.name,
                self._config.namespace,
                propagation_policy="Foreground",
            )
        except Exception as e:
            # failure is probably ok here, if the job doesn't exist
            logging.debug(sys.exc_info()[0])
            logging.debug(e)
