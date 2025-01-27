from dbt.logger import GLOBAL_LOGGER as logger
from dbt import version as dbt_version
from snowplow_tracker import Subject, Tracker, Emitter, logger as sp_logger
from snowplow_tracker import SelfDescribingJson
from datetime import datetime

import pytz
import platform
import uuid
import requests
import yaml
import os

sp_logger.setLevel(100)

COLLECTOR_URL = "fishtownanalytics.sinter-collect.com"
COLLECTOR_PROTOCOL = "https"

INVOCATION_SPEC = 'iglu:com.dbt/invocation/jsonschema/1-0-1'
PLATFORM_SPEC = 'iglu:com.dbt/platform/jsonschema/1-0-0'
RUN_MODEL_SPEC = 'iglu:com.dbt/run_model/jsonschema/1-0-1'
INVOCATION_ENV_SPEC = 'iglu:com.dbt/invocation_env/jsonschema/1-0-0'
PACKAGE_INSTALL_SPEC = 'iglu:com.dbt/package_install/jsonschema/1-0-0'
RPC_REQUEST_SPEC = 'iglu:com.dbt/rpc_request/jsonschema/1-0-1'

DBT_INVOCATION_ENV = 'DBT_INVOCATION_ENV'


class TimeoutEmitter(Emitter):
    def __init__(self):
        super().__init__(COLLECTOR_URL, protocol=COLLECTOR_PROTOCOL,
                         buffer_size=1, on_failure=self.handle_failure)

    @staticmethod
    def handle_failure(num_ok, unsent):
        # num_ok will always be 0, unsent will always be 1 entry long, because
        # the buffer is length 1, so not much to talk about
        logger.warning('Error sending message, disabling tracking')
        do_not_track()

    def http_get(self, payload):
        sp_logger.info("Sending GET request to {}...".format(self.endpoint))
        sp_logger.debug("Payload: {}".format(payload))
        r = requests.get(self.endpoint, params=payload, timeout=5.0)

        msg = "GET request finished with status code: " + str(r.status_code)
        if self.is_good_status_code(r.status_code):
            sp_logger.info(msg)
        else:
            sp_logger.warning(msg)
        return r


emitter = TimeoutEmitter()
tracker = Tracker(emitter, namespace="cf", app_id="dbt")

active_user = None


class User:

    def __init__(self, cookie_dir):
        self.do_not_track = True
        self.cookie_dir = cookie_dir

        self.id = None
        self.invocation_id = str(uuid.uuid4())
        self.run_started_at = datetime.now(tz=pytz.utc)

    def state(self):
        return "do not track" if self.do_not_track else "tracking"

    @property
    def cookie_path(self):
        return os.path.join(self.cookie_dir, '.user.yml')

    def initialize(self):
        self.do_not_track = False

        cookie = self.get_cookie()
        self.id = cookie.get('id')

        subject = Subject()
        subject.set_user_id(self.id)
        tracker.set_subject(subject)

    def set_cookie(self):
        # If the user points dbt to a profile directory which exists AND
        # contains a profiles.yml file, then we can set a cookie. If the
        # specified folder does not exist, or if there is not a profiles.yml
        # file in this folder, then an inconsistent cookie can be used. This
        # will change in every dbt invocation until the user points to a
        # profile dir file which contains a valid profiles.yml file.
        #
        # See: https://github.com/fishtown-analytics/dbt/issues/1645

        user = {"id": str(uuid.uuid4())}

        cookie_path = os.path.abspath(self.cookie_dir)
        profiles_file = os.path.join(cookie_path, 'profiles.yml')
        if os.path.exists(cookie_path) and os.path.exists(profiles_file):
            with open(self.cookie_path, "w") as fh:
                yaml.dump(user, fh)

        return user

    def get_cookie(self):
        if not os.path.isfile(self.cookie_path):
            user = self.set_cookie()
        else:
            with open(self.cookie_path, "r") as fh:
                try:
                    user = yaml.safe_load(fh)
                    if user is None:
                        user = self.set_cookie()
                except yaml.reader.ReaderError:
                    user = self.set_cookie()
        return user


def get_run_type(args):
    return 'regular'


def get_invocation_context(user, config, args):
    # put this in here to avoid an import cycle
    from dbt.adapters.factory import get_adapter
    try:
        adapter_type = get_adapter(config).type()
    except Exception:
        adapter_type = None

    return {
        "project_id": None if config is None else config.hashed_name(),
        "user_id": user.id,
        "invocation_id": user.invocation_id,

        "command": args.which,
        "options": None,
        "version": str(dbt_version.installed),

        "run_type": get_run_type(args),
        "adapter_type": adapter_type,
    }


def get_invocation_start_context(user, config, args):
    data = get_invocation_context(user, config, args)

    start_data = {
        "progress": "start",
        "result_type": None,
        "result": None
    }

    data.update(start_data)
    return SelfDescribingJson(INVOCATION_SPEC, data)


def get_invocation_end_context(user, config, args, result_type):
    data = get_invocation_context(user, config, args)

    start_data = {
        "progress": "end",
        "result_type": result_type,
        "result": None
    }

    data.update(start_data)
    return SelfDescribingJson(INVOCATION_SPEC, data)


def get_invocation_invalid_context(user, config, args, result_type):
    data = get_invocation_context(user, config, args)

    start_data = {
        "progress": "invalid",
        "result_type": result_type,
        "result": None
    }

    data.update(start_data)
    return SelfDescribingJson(INVOCATION_SPEC, data)


def get_platform_context():
    data = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_version": platform.python_implementation(),
    }

    return SelfDescribingJson(PLATFORM_SPEC, data)


def get_dbt_env_context():
    default = 'manual'

    dbt_invocation_env = os.getenv(DBT_INVOCATION_ENV, default)
    if dbt_invocation_env == '':
        dbt_invocation_env = default

    data = {
        "environment": dbt_invocation_env,
    }

    return SelfDescribingJson(INVOCATION_ENV_SPEC, data)


def track(user, *args, **kwargs):
    if user.do_not_track:
        return
    else:
        logger.debug("Sending event: {}".format(kwargs))
        try:
            tracker.track_struct_event(*args, **kwargs)
        except Exception:
            logger.debug(
                "An error was encountered while trying to send an event"
            )


def track_invocation_start(config=None, args=None):
    context = [
        get_invocation_start_context(active_user, config, args),
        get_platform_context(),
        get_dbt_env_context()
    ]

    track(
        active_user,
        category="dbt",
        action='invocation',
        label='start',
        context=context
    )


def track_model_run(options):
    context = [SelfDescribingJson(RUN_MODEL_SPEC, options)]

    track(
        active_user,
        category="dbt",
        action='run_model',
        label=active_user.invocation_id,
        context=context
    )


def track_rpc_request(options):
    context = [SelfDescribingJson(RPC_REQUEST_SPEC, options)]

    track(
        active_user,
        category="dbt",
        action='rpc_request',
        label=active_user.invocation_id,
        context=context
    )


def track_package_install(options):
    context = [SelfDescribingJson(PACKAGE_INSTALL_SPEC, options)]
    track(
        active_user,
        category="dbt",
        action='package',
        label=active_user.invocation_id,
        property_='install',
        context=context
    )


def track_invocation_end(
        config=None, args=None, result_type=None
):
    user = active_user
    context = [
        get_invocation_end_context(user, config, args, result_type),
        get_platform_context(),
        get_dbt_env_context()
    ]
    track(
        active_user,
        category="dbt",
        action='invocation',
        label='end',
        context=context
    )


def track_invalid_invocation(
        config=None, args=None, result_type=None
):

    user = active_user
    invocation_context = get_invocation_invalid_context(
        user,
        config,
        args,
        result_type
    )

    context = [
        invocation_context,
        get_platform_context(),
        get_dbt_env_context()
    ]

    track(
        active_user,
        category="dbt",
        action='invocation',
        label='invalid',
        context=context
    )


def flush():
    logger.debug("Flushing usage events")
    tracker.flush()


def do_not_track():
    global active_user
    active_user = User(None)


def initialize_tracking(cookie_dir):
    global active_user
    active_user = User(cookie_dir)
    try:
        active_user.initialize()
    except Exception:
        logger.debug('Got an exception trying to initialize tracking',
                     exc_info=True)
        active_user = User(None)
