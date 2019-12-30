import sys
from enum import Enum


class Phase(Enum):
    NONE = 0
    PREPARE = 1
    BUILD = 2
    DONE = 10


class StepState(Enum):
    UNKNOWN = 0
    NOTSTARTED = 1

    PREFLIGHT = 2
    PENDING = 3
    RUNNING = 4
    POSTFLIGHT = 5
    STOPPING = 6

    SUCCEEDED = 7
    FAILED = 8
    CANCELLED = 9

    @property
    def active(self):
        return self.NOTSTARTED.value < self.value <= self.STOPPING.value

    @property
    def completed(self):
        return self.value >= self.SUCCEEDED.value


class Message(Enum):
    PHASE_UPDATE = 0
    STATE_UPDATE = 1

    MANAGER_MSG = 11     # data is a list of strings
    CONTAINER_MSG = 12   # data is a list of strings
    RESULT_MSG = 13      # data is a tuple (code: int, error: str)
    PROGRESS_START = 14  # data is a string
    PROGRESS_MSG = 15    # data is a tuple (msg: string, progress: int)


# Step states in different phases
# none:
#   - observer is created, but has not entered any phases
#
# prepare:
#   preflight   - unused / skipped
#   pending     - unused / skipped
#   running     - preparing the step
#   postflight  - unused / skipped
#   stopping    - stopping step preparation as it was cancelled
#   succeeded   - step preparation completed
#   failed      - step preparation failed
#   cancelled   - the preparation was cancelled, before this step was completed
#
# build:
#   preflight   - running pre-step actions
#   pending     - the step is not yet running for various reasons, e.g. waiting for resources or container is being created
#   running     - the step is active and running
#   postflight  - running post-step actions
#   stopping    - stopping the step as it was cancelled
#   succeeded   - the step returned 0
#   failed      - the step returned non-zero status
#   cancelled   - the build was cancelled, before this step was completed
#
# done:
#   - build has entered done phase


class BuildObserver:
    def __init__(self):
        self._phase = Phase.NONE
        self._states = {}

    def get_step_state(self, step):
        return self._states.get(step, StepState.UNKNOWN)

    def _message(self, phase, type_, step=None, state=None, data=None):
        raise NotImplementedError

    # Phase transitions, synchronous

    def _phase_update(self, phase):
        if self._phase != phase:
            self._phase = phase
            self._states = {step: StepState.NOTSTARTED for step in self._states}
            self._message(self._phase, Message.PHASE_UPDATE)

    def enter_prepare(self):
        self._phase_update(Phase.PREPARE)

    def enter_build(self):
        self._phase_update(Phase.BUILD)

    def done(self, data=None):
        self._phase_update(Phase.DONE)

    # Step transitions, can be async

    def _state_update(self, step, state):
        if self._phase == Phase.NONE:
            raise RuntimeError(
                "%s has not entered any phase when requested to update state to %s for step %r"
                % (self.__class__.__name__, state, step))
        if self.get_step_state(step) != state:
            self._states[step] = state
            self._message(self._phase, Message.STATE_UPDATE, step, state)

    def step_preflight(self, step):
        self._state_update(step, StepState.PREFLIGHT)

    def step_pending(self, step):
        self._state_update(step, StepState.PENDING)

    def step_running(self, step):
        self._state_update(step, StepState.RUNNING)

    def step_postflight(self, step):
        self._state_update(step, StepState.POSTFLIGHT)

    def step_stopping(self, step):
        self._state_update(step, StepState.STOPPING)

    def step_succeeded(self, step):
        self._state_update(step, StepState.SUCCEEDED)

    def step_failed(self, step):
        self._state_update(step, StepState.FAILED)

    def step_cancelled(self, step):
        self._state_update(step, StepState.CANCELLED)

    # In step state messages

    def _send_message(self, type_, step, msg):
        if self._phase == Phase.NONE:
            raise RuntimeError(
                "%s has not entered any phase when requested to send message %s in step %r with content %r"
                % (self.__class__.__name__, type_, step, msg))
        state = self.get_step_state(step)
        self._message(self._phase, type_, step, state, msg)

    def manager_msg(self, step, msg):
        msg = msg.rstrip().splitlines()
        self._send_message(Message.MANAGER_MSG, step, msg)

    def container_msg(self, step, msg):
        msg = msg.rstrip().splitlines()
        self._send_message(Message.CONTAINER_MSG, step, msg)

    def result_msg(self, result):
        self._send_message(Message.RESULT_MSG, result.step, (result.code, result.error))

    def start_progress(self, step, msg):
        self._send_message(Message.PROGRESS_START, step, "%s [ 0%%]" % (msg,))

    def notify_progress(self, step, msg, progress):
        self._send_message(Message.PROGRESS_MSG, step, (msg, progress))


ENTER_STATE_TEXTS = {
    StepState.PREFLIGHT: "Pre-Flight tasks..",
    StepState.PENDING: "Pending..",
    StepState.RUNNING: "Running..",
    StepState.POSTFLIGHT: "Post-Flight tasks..",
    StepState.FAILED: "Failed!",
    StepState.CANCELLED: "\rCancelled..",
}
class StreamObserver(BuildObserver):
    def __init__(self, stream=None):
        super().__init__()
        self.stream = stream or sys.stdout

    def _message(self, phase, type_, step=None, state=None, data=None):
        fmt = "%s %s\n"
        if type_ == Message.STATE_UPDATE:
            data = ENTER_STATE_TEXTS.get(state)
            if data is None:
                return
        elif type_ == Message.CONTAINER_MSG:
            fmt = "%s >> %s\n"
        elif type_ == Message.MANAGER_MSG:
            fmt = "%s : %s\n"
        elif type_ == Message.PROGRESS_START:
            fmt = "%s : %s"
        elif type_ == Message.PROGRESS_MSG:
            if data[1] == 100:
                fmt = "\r%s : %s\n"
            else:
                fmt = "\r%s : %s"
            data = "%s [%d%%]" % data
        else:
            return
        phase_s = phase.name
        if step is not None:
            phase_s += ' ' + str(step)
        if isinstance(data, str):
            data = (data,)
        for line in data:
            self.stream.write(fmt % (phase_s, line))
