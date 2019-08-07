import sys
from enum import Enum
from time import time

from colorama import init as init_color, Fore, Style

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

    MANAGER_MSG = 11   # data is a list of strings
    CONTAINER_MSG = 12 # data is a list of strings
    RESULT_MSG = 13    # data is a tuple (code: int, error: str)


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
        cur_state = self.get_step_state(step)
        if cur_state != state and not cur_state.completed:
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
        state = (StepState.SUCCEEDED if result.ok else
                 StepState.CANCELLED if result.cancelled else
                 StepState.FAILED)
        # NOTE: if for some reason some step isn't marked as succeeded,
        # this will result in the 'ok: [time]' message, without any
        # indication what step is in question and with an incorrect time
        if state == StepState.SUCCEEDED:
            for step in self._states:
                self.step_succeeded(step)
        else:
            self._state_update(result.step, state)
        self._send_message(Message.RESULT_MSG, result.step, (result.code, result.error))


class StreamObserver(BuildObserver):
    def __init__(self, stream=None, colors=True):
        super().__init__()
        self._stream = stream or sys.stdout
        self._colors = colors
        self._start_time = -1
        init_color()

    def _write(self, to_write, colors=None):
        if not isinstance(to_write, str):
            to_write = str(to_write)
        if self._colors:
            if colors:
                self._stream.write(colors)
            self._stream.write(to_write + Style.RESET_ALL)
        else:
            self._stream.write(to_write)

    def _message(self, phase, type_, step=None, state=None, data=None):
        def format_time(duration):
            duration = int(duration)
            hours, seconds = divmod(duration, 3600)
            minutes, seconds = divmod(seconds, 60)
            hours = '{}h '.format(hours) if hours else ''
            minutes = '{}min '.format(minutes) if minutes else ''
            seconds = '{}s'.format(seconds)
            return '{}{}{}'.format(hours, minutes, seconds)

        if type_ == Message.PHASE_UPDATE:
            if phase == Phase.PREPARE:
                self._write("PREPARING STEPS\n\n", Style.BRIGHT)
            elif phase == Phase.BUILD:
                self._write("BUILDING STEPS\n\n", Style.BRIGHT)
        elif type_ == Message.STATE_UPDATE:
            if state == StepState.SUCCEEDED:
                self._write('  ok: ', Fore.GREEN + Style.BRIGHT)
                self._write('{}\n\n'.format(format_time(time() - self._start_time)))
            elif state in (StepState.PENDING, StepState.PREFLIGHT):
                self._write('step ', Fore.CYAN + Style.BRIGHT)
                self._write('%s\n' % (step,), Style.BRIGHT)
            elif phase == Phase.BUILD and state == StepState.RUNNING:
                self._write("  Running container\n", Fore.BLUE + Style.BRIGHT)
            self._start_time = time()
        elif type_ == Message.RESULT_MSG:
            if state == StepState.CANCELLED:
                msg = ("\r  \nBuild cancelled{}\n"
                    .format(" on step %s" % (step,) if step else ""))
                self._write(msg, Fore.RED + Style.BRIGHT)
                return
            if state == StepState.FAILED:
                self._write('  failed: ', Fore.RED + Style.BRIGHT)
                self._write('{}\n\n'.format(format_time(time() - self._start_time)))
            elif phase == Phase.PREPARE:
                return
            if data[0] == 0:
                self._write("Build OK\n", Fore.GREEN + Style.BRIGHT)
            else:
                self._write("Build failed on step %s: exit code %d\n"
                    % (step, data[0]), Fore.RED + Style.BRIGHT)
        else:
            color = None
            if type_ == Message.CONTAINER_MSG:
                fmt = "  >> %s\n"
            elif type_ == Message.MANAGER_MSG:
                fmt = "  %s\n"
                color = Fore.BLUE + Style.BRIGHT
            else:
                return

            if isinstance(data, str):
                data = (data,)
            for line in data:
                self._write(fmt % (line,), color)
