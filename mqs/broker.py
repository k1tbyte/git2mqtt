import uuid

from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

from .config import Config

QOS = mqtt.QoS.AT_LEAST_ONCE
TIMEOUT = 30


def _resumed(connection, return_code, session_present, **_):
    # Clean sessions lose subscriptions on reconnect; a long-running serve must get them back.
    if not session_present:
        connection.resubscribe_existing_topics()


class Broker:
    """AWS IoT over SigV4 websockets, or plain `tcp://host:port` for local tests."""

    def __init__(self, cfg: Config):
        client_id = f"mqs-{uuid.uuid4().hex}"
        if cfg.endpoint.startswith("tcp://"):
            host, port = cfg.endpoint[6:].rsplit(":", 1)
            self._conn = mqtt.Connection(
                client=mqtt.Client(None, None), host_name=host, port=int(port), client_id=client_id,
                clean_session=True, keep_alive_secs=30, on_connection_resumed=_resumed,
            )
        else:
            creds = (auth.AwsCredentialsProvider.new_profile(profile_name=cfg.profile) if cfg.profile
                     else auth.AwsCredentialsProvider.new_default_chain())
            self._conn = mqtt_connection_builder.websockets_with_default_aws_signing(
                endpoint=cfg.endpoint, region=cfg.region, credentials_provider=creds, client_id=client_id,
                clean_session=True, keep_alive_secs=30, on_connection_resumed=_resumed,
            )
        self._conn.connect().result(TIMEOUT)

    def publish(self, topic: str, payload: bytes):
        future, _ = self._conn.publish(topic, payload, QOS)
        future.result(TIMEOUT)

    def subscribe(self, topic: str, callback):
        future, _ = self._conn.subscribe(topic, QOS, lambda topic, payload, **_: callback(topic, payload))
        future.result(TIMEOUT)

    def close(self):
        self._conn.disconnect().result(TIMEOUT)
