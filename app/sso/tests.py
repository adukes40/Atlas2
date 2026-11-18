import base64
import datetime as dt
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from .models import ClassLinkConfig

User = get_user_model()
IDP = "https://idp.example.com/saml"
HOST = "atlas.example.org"
ACS = f"https://{HOST}/sso/classlink/acs/"
SP = f"https://{HOST}/sso/classlink/metadata/"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test idp")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pem_cert = cert.public_bytes(serialization.Encoding.PEM).decode()
    return pem_key, pem_cert


KEY, CERT = _keypair()
OTHER_KEY, OTHER_CERT = _keypair()
CERT_BODY = "".join(CERT.strip().splitlines()[1:-1])

METADATA = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{IDP}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:X509Data>
      <ds:X509Certificate>{CERT_BODY}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{IDP}/sso"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def saml_response(email="pat@schools.example", key=KEY, cert=CERT, attributes=True, audience=SP, assertion_id=None):
    now = dt.datetime.now(dt.UTC)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    issued, later = now.strftime(fmt), (now + dt.timedelta(minutes=5)).strftime(fmt)
    assertion_id = assertion_id or "_" + uuid.uuid4().hex
    attrs = (
        f"""<saml:AttributeStatement>
          <saml:Attribute Name="email"><saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>
          <saml:Attribute Name="firstName"><saml:AttributeValue>Pat</saml:AttributeValue></saml:Attribute>
          <saml:Attribute Name="lastName"><saml:AttributeValue>Lee</saml:AttributeValue></saml:Attribute>
        </saml:AttributeStatement>"""
        if attributes else ""
    )
    assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{assertion_id}" Version="2.0" IssueInstant="{issued}">
      <saml:Issuer>{IDP}</saml:Issuer>
      <saml:Subject>
        <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified">pat.lee</saml:NameID>
        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
          <saml:SubjectConfirmationData NotOnOrAfter="{later}" Recipient="{ACS}"/>
        </saml:SubjectConfirmation>
      </saml:Subject>
      <saml:Conditions NotBefore="{issued}" NotOnOrAfter="{later}">
        <saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>
      </saml:Conditions>
      <saml:AuthnStatement AuthnInstant="{issued}" SessionIndex="{assertion_id}">
        <saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:unspecified</saml:AuthnContextClassRef></saml:AuthnContext>
      </saml:AuthnStatement>
      {attrs}
    </saml:Assertion>"""
    signed = OneLogin_Saml2_Utils.add_sign(assertion, key, cert).decode() if key else assertion
    signed = signed.replace('<?xml version="1.0"?>', "").strip()
    response = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
      ID="_{uuid.uuid4().hex}" Version="2.0" IssueInstant="{issued}" Destination="{ACS}">
      <saml:Issuer>{IDP}</saml:Issuer>
      <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
      {signed}
    </samlp:Response>"""
    return base64.b64encode(response.encode()).decode()


@override_settings(ATLAS_HTTPS_HOST=HOST, ALLOWED_HOSTS=[HOST])
class ClassLinkSignInTests(TestCase):
    def setUp(self):
        ClassLinkConfig.objects.create(enabled=True, metadata=METADATA, create_accounts=True)

    def post(self, data, relay=""):
        return self.client.post(reverse("saml-acs"), {"SAMLResponse": data, "RelayState": relay}, HTTP_HOST=HOST, secure=True)

    def test_first_sign_in_creates_a_technician(self):
        response = self.post(saml_response())
        self.assertRedirects(response, reverse("device360"), fetch_redirect_response=False)
        user = User.objects.get(email="pat@schools.example")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.first_name, user.last_name), ("Pat", "Lee"))

    def test_matches_an_existing_person_by_email_and_keeps_their_role(self):
        admin = User.objects.create_superuser("pat", email="Pat@Schools.example", password="a-long-test-password")
        self.post(saml_response())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.pk)
        self.assertTrue(admin.has_usable_password())

    def test_refuses_unknown_people_when_account_creation_is_off(self):
        ClassLinkConfig.objects.filter(pk=1).update(create_accounts=False)
        response = self.post(saml_response())
        self.assertContains(response, "do not have an Atlas account", status_code=403)
        self.assertFalse(User.objects.exists())

    def test_refuses_a_turned_off_account(self):
        User.objects.create_user("pat", email="pat@schools.example", is_active=False)
        self.assertContains(self.post(saml_response()), "turned off", status_code=403)

    def test_refuses_a_response_signed_by_someone_else(self):
        response = self.post(saml_response(key=OTHER_KEY, cert=OTHER_CERT))
        self.assertContains(response, "could not be checked", status_code=403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_refuses_an_unsigned_response(self):
        self.assertContains(self.post(saml_response(key=None)), "could not be checked", status_code=403)

    def test_refuses_a_response_meant_for_another_app(self):
        response = self.post(saml_response(audience="https://other.example.com/"))
        self.assertEqual(response.status_code, 403)

    def test_refuses_a_replay(self):
        data = saml_response()
        self.post(data)
        self.client.logout()
        self.assertContains(self.post(data), "already used", status_code=403)

    def test_falls_back_to_an_email_name_id_and_needs_an_email(self):
        self.assertContains(self.post(saml_response(attributes=False)), "did not send an email", status_code=403)

    def test_relay_state_only_goes_to_atlas_pages(self):
        response = self.post(saml_response(), relay="https://evil.example.com/")
        self.assertRedirects(response, reverse("device360"), fetch_redirect_response=False)
        response = self.post(saml_response(), relay="/reports/")
        self.assertRedirects(response, "/reports/", fetch_redirect_response=False)

    def test_refused_when_turned_off(self):
        ClassLinkConfig.objects.filter(pk=1).update(enabled=False)
        self.assertContains(self.post(saml_response()), "turned off in Atlas", status_code=403)

    def test_sign_in_page_offers_classlink_and_start_redirects_to_it(self):
        self.assertContains(self.client.get(reverse("login"), HTTP_HOST=HOST, secure=True), "Sign in with ClassLink")
        response = self.client.get(reverse("saml-start"), HTTP_HOST=HOST, secure=True)
        self.assertTrue(response["Location"].startswith(f"{IDP}/sso?SAMLRequest="))

    def test_metadata_names_atlas_addresses(self):
        response = self.client.get(reverse("saml-metadata"), HTTP_HOST=HOST, secure=True)
        self.assertContains(response, f'entityID="{SP}"')
        self.assertContains(response, ACS)


@override_settings(ATLAS_HTTPS_HOST=HOST, ALLOWED_HOSTS=[HOST])
class ClassLinkSettingsTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin"))

    def get(self, **kw):
        return self.client.get(reverse("settings-classlink"), HTTP_HOST=HOST, secure=True, **kw)

    def save(self, **data):
        return self.client.post(reverse("settings-classlink"), data, HTTP_HOST=HOST, secure=True)

    def test_is_administrators_only(self):
        self.client.force_login(User.objects.create_user("tech"))
        self.assertEqual(self.get().status_code, 403)

    def test_shows_values_to_copy(self):
        self.assertContains(self.get(), ACS)

    def test_saves_and_turns_on(self):
        self.save(metadata=METADATA, enabled="on", create_accounts="on")
        self.assertTrue(ClassLinkConfig.load().ready)

    def test_rejects_bad_metadata_and_on_without_metadata(self):
        self.assertContains(self.save(metadata="<nope/>"), "no sign-in address")
        self.assertContains(self.save(enabled="on"), "before turning sign-in on")
        self.assertFalse(ClassLinkConfig.load().ready)

    def test_password_sign_in_still_works_when_classlink_is_on(self):
        ClassLinkConfig.objects.create(enabled=True, metadata=METADATA)
        self.client.logout()
        User.objects.create_user("tech", password="a-long-test-password")
        response = self.client.post(reverse("login"), {"username": "tech", "password": "a-long-test-password"}, HTTP_HOST=HOST, secure=True)
        self.assertRedirects(response, reverse("device360"), fetch_redirect_response=False)
