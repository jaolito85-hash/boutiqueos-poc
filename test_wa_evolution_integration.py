import os
import unittest
from unittest.mock import patch

from flask import Flask

from wa import evolution_client
from wa.webhook import bp_wa_webhook


class EvolutionWebhookConfigTests(unittest.TestCase):
    def test_build_webhook_url_uses_explicit_env_url(self):
        with patch.dict(os.environ, {"EVOLUTION_WEBHOOK_URL": "https://painel.haus.com/webhook/wa/"}, clear=True):
            self.assertEqual(
                evolution_client.webhook_url(),
                "https://painel.haus.com/webhook/wa",
            )

    def test_build_webhook_url_from_public_panel_url(self):
        with patch.dict(os.environ, {"HAUS_PUBLIC_URL": "https://painel.haus.com/base/"}, clear=True):
            self.assertEqual(
                evolution_client.webhook_url(),
                "https://painel.haus.com/base/webhook/wa",
            )

    @patch("wa.evolution_client.requests.post")
    def test_configure_webhook_sends_single_endpoint_evolution_payload(self, post):
        post.return_value.status_code = 201
        post.return_value.json.return_value = {"webhook": {"ok": True}}

        with patch.dict(
            os.environ,
            {
                "EVOLUTION_API_URL": "https://evo.haus.com/",
                "EVOLUTION_INSTANCE_NAME": "haus-demo",
                "EVOLUTION_API_KEY": "secret",
                "EVOLUTION_WEBHOOK_URL": "https://painel.haus.com/webhook/wa",
            },
            clear=True,
        ):
            result = evolution_client.configure_webhook()

        self.assertEqual(result, {"webhook": {"ok": True}})
        post.assert_called_once()
        url = post.call_args.args[0]
        self.assertEqual(url, "https://evo.haus.com/webhook/set/haus-demo")
        self.assertEqual(post.call_args.kwargs["headers"]["apikey"], "secret")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "enabled": True,
                "url": "https://painel.haus.com/webhook/wa",
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE"],
            },
        )

    @patch("wa.evolution_client.requests.post")
    def test_configure_webhook_retries_legacy_nested_payload_when_required(self, post):
        first = unittest.mock.Mock()
        first.status_code = 400
        first.text = '{"message":[["instance requires property \\"webhook\\""]]}'
        second = unittest.mock.Mock()
        second.status_code = 201
        second.json.return_value = {"webhook": {"ok": True}}
        post.side_effect = [first, second]

        with patch.dict(
            os.environ,
            {
                "EVOLUTION_API_URL": "https://evo.haus.com",
                "EVOLUTION_INSTANCE_NAME": "haus-demo",
                "EVOLUTION_API_KEY": "secret",
                "EVOLUTION_WEBHOOK_URL": "https://painel.haus.com/webhook/wa",
            },
            clear=True,
        ):
            result = evolution_client.configure_webhook()

        self.assertEqual(result, {"webhook": {"ok": True}})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args.kwargs["json"],
            {
                "webhook": {
                    "enabled": True,
                    "url": "https://painel.haus.com/webhook/wa",
                    "webhookByEvents": False,
                    "webhookBase64": False,
                    "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE"],
                }
            },
        )


class EvolutionWebhookReceiverTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(bp_wa_webhook)
        self.client = app.test_client()

    @patch("wa.webhook.record_wa_message")
    @patch("wa.webhook.upsert_wa_contact")
    def test_by_event_messages_upsert_url_is_accepted(self, upsert_contact, record_message):
        upsert_contact.return_value = 12

        response = self.client.post(
            "/webhook/wa/messages-upsert",
            json={
                "data": {
                    "key": {
                        "remoteJid": "5544999998888@s.whatsapp.net",
                        "fromMe": False,
                        "id": "MSG-1",
                    },
                    "pushName": "Cliente Demo",
                    "message": {"conversation": "Oi, ainda tem a linha Toile?"},
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        upsert_contact.assert_called_once_with("5544999998888", "Cliente Demo")
        record_message.assert_called_once()
        self.assertEqual(record_message.call_args.kwargs["contact_id"], 12)
        self.assertEqual(record_message.call_args.kwargs["direction"], "in")
        self.assertEqual(record_message.call_args.kwargs["content"], "Oi, ainda tem a linha Toile?")


if __name__ == "__main__":
    unittest.main()
