import sys
import logging
import threading

from types import SimpleNamespace
import datetime
import pytz

from mozalert.status import EnumState, EnumStatus, Status
from mozalert.metrics import MetricsQueueItem


class BaseCheck:
    """
    BaseCheck implements the thread/interval logic of a check without any
    actual execution.

    To use this class as your base class, you should implement the
    job-related methods:
        * delete_job
        * get_job_logs
        * get_job_status (SimpleNamespace)
        * set_crd_status
        * run_job
    """

    def __init__(self, **kwargs):
        """
        initialize a check
        """

        self._job_poll_interval = float(kwargs.get("job_poll_interval", 3))
        # if the process is restarted the status is re-read
        # from k8s and fed into the new check
        # this is removed from the object once its read
        self._pre_status = kwargs.get("pre_status", {})
        self.metrics_queue = kwargs.get("metrics_queue", None)

        self.config = SimpleNamespace(
            name=kwargs.get("name"),
            namespace=kwargs.get("namespace"),
            check_interval=float(kwargs.get("check_interval")),
            retry_interval=float(kwargs.get("retry_interval", 0)),
            notification_interval=float(kwargs.get("notification_interval", 0)),
            escalations=kwargs.get("escalations", []),
            max_attempts=int(kwargs.get("max_attempts", "3")),
            timeout=float(kwargs.get("timeout", 0)),
        )

        if not self.config.retry_interval:
            self.config.retry_interval = self.config.check_interval
        if not self.config.notification_interval:
            self.config.notification_interval = self.config.check_interval

        self.shutdown = False
        self._runtime = datetime.timedelta(seconds=0)
        self._thread = None
        self.escalated = False
        self._next_interval = self.config.check_interval

        self._status = Status(status=EnumStatus.PENDING, state=EnumState.IDLE)

        if self._pre_status:
            self.status.status = self._pre_status.get("status", self.status.status)
            self.status.state = self._pre_status.get("state", self.status.state)
            self.status.last_check = self._pre_status.get(
                "lastCheckTimestamp", self.status.last_check
            )
            self.status.next_check = self._pre_status.get(
                "nextCheckTimestamp", self.status.next_check
            )
            self.status.attempt = self._pre_status.get("attempt", self.status.attempt)
            self.status.logs = self._pre_status.get("logs", self.status.logs)
            if self.status.RUNNING:
                # when the pre_status was created a check was running,
                # that check is dead to us so we need to just decrement our attempt,
                # and reschedule the check ASAP
                self._next_interval = 1
                if self.status.attempt:
                    self.status.attempt -= 1
            elif self.status.next_check:
                # check was not running, so set the interval based
                # on the original next_check
                next_check = pytz.utc.localize(self.status.next_check)
                now = pytz.utc.localize(datetime.datetime.utcnow())
                if now > next_check:
                    # the check was in the process of starting
                    # when the controller restarted
                    self._next_interval = 1
                else:
                    self._next_interval = (next_check - now).seconds
            self._pre_status = {}

        self.start_thread()
        self.set_crd_status()

    @property
    def config(self):
        return self._config

    @property
    def status(self):
        return self._status

    @property
    def thread(self):
        return self._thread

    @property
    def shutdown(self):
        return self._shutdown

    @property
    def escalated(self):
        return self._escalated

    @escalated.setter
    def escalated(self, escalated):
        self._escalated = escalated

    @shutdown.setter
    def shutdown(self, shutdown):
        self._shutdown = shutdown

    @config.setter
    def config(self, config):
        self._config = config

    def __repr__(self):
        return f"{self.config.namespace}/{self.config.name}"

    def run_job(self):
        logging.info("Executing mock run_job")

    def set_crd_status(self):
        logging.info("Executing mock set_crd_status")

    def get_job_status(self):
        logging.info("Executing mock set_status")
        return SimpleNamespace(
            active=False, succeeded=False, failed=False, start_time=None
        )

    def get_job_logs(self):
        logging.info("Executing mock get_job_logs")
        return ""

    def delete_job(self):
        logging.info("Executing mock delete_job")

    def escalate(self, recovery=False):
        self.escalated = not recovery
        logging.info("Executing mock escalation")

    def terminate(self, join=False):
        """
        stop the thread and cleanup any leftover jobs
        """
        self.shutdown = True
        logging.debug("Stopping check thread")
        if self._thread:
            try:
                self._thread.cancel()
            except Exception as e:
                logging.info(sys.exc_info()[0])
                logging.info(e)

        self.delete_job()

        if join:
            self.join()

    def join(self):
        if self._thread:
            return self._thread.join()

    def check(self):
        """
        main thread for creating then watching a check job; this is called as
        the Timer thread target.
        """
        self.status.attempt += 1
        logging.info(f"Starting check attempt {self.status.attempt}")
        # run the job; this blocks until completion
        try:
            self.run_job()
        except Exception as e:
            logging.info(sys.exc_info()[0])
            logging.info(e)
            self.delete_job()
        logging.info("Check finished")
        logging.debug("Cleaning up finished job")
        self.delete_job()

        __labels = {
            "name": self.config.name,
            "namespace": self.config.namespace,
            "status": self.status.status.name,
            "escalated": self.escalated,
        }
        if self.status.OK and self.escalated:
            # recovery!
            self.escalate(recovery=True)
            self.status.attempt = 0
            self._next_interval = self.config.check_interval
        elif self.status.OK:
            # check passed, things are great!
            self.status.attempt = 0
            self._next_interval = self.config.check_interval
        elif self.status.attempt >= self.config.max_attempts:
            # state is not OK and we've run out of attempts. do the escalation
            self.escalate()
            self._next_interval = self.config.notification_interval
            # ^ TODO keep retrying after escalation? giveup? reset?
        else:
            # not state OK and not enough failures to escalate
            self._next_interval = self.config.retry_interval

        if self.metrics_queue:
            self.metrics_queue.put(
                MetricsQueueItem(
                    "mozalert_check_runtime", **__labels, value=self._runtime.seconds,
                )
            )
            self.metrics_queue.put(
                MetricsQueueItem(
                    f"mozalert_check_{self.status.status.name}_count", **__labels
                )
            )
            self.metrics_queue.put(
                MetricsQueueItem(
                    "mozalert_check_escalations", **__labels, value=int(self.escalated),
                )
            )

        # set the next_check for the CRD status
        self.status.next_check = pytz.utc.localize(
            datetime.datetime.utcnow()
        ) + datetime.timedelta(seconds=self._next_interval)

        if not self.shutdown:
            # schedule the next run
            self.start_thread()
            # update the CRD status subresource
            self.set_crd_status()

    def start_thread(self):
        """
        starts the thread and updates the next_check time in the object.

        For this to work you must have a self.check and a self._next_interval >=0 seconds
        """
        logging.info(
            f"Starting {self} thread at interval {self._next_interval} seconds"
        )

        self._thread = threading.Timer(self._next_interval, self.check)
        self._thread.setName(f"{self}")
        self._thread.start()

        self.status.next_check = pytz.utc.localize(
            datetime.datetime.utcnow()
        ) + datetime.timedelta(seconds=self._next_interval)
