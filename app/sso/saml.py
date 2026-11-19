"""The only code that talks SAML. Wraps python3-saml for ClassLink.

ClassLink never calls Atlas directly (Atlas may sit on a private network):
everything travels through the person's browser, and ClassLink's metadata is
pasted in or fetched by Atlas.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
from onelogin.saml2.settings import OneLogin_Saml2_Settings

EMAIL_KEYS = ("email", "mail", "emailaddress", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
FIRST_KEYS = ("firstname", "givenname", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname")
LAST_KEYS = ("lastname", "familyname", "surname", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname")


class SignInRefused(Exception):
    """The message is written to be shown to the person signing in."""


def base_url(request):
    host = settings.ATLAS_HTTPS_HOST
    return f"https://{host}" if host else f"{request.scheme}://{request.get_host()}"


def sp_values(request):
    """What the ClassLink admin copies into ClassLink."""
    base = base_url(request)
    return {"entity_id": base + reverse("saml-metadata"), "acs_url": base + reverse("saml-acs")}


def parse_metadata(xml):
    """ClassLink's metadata as python3-saml settings. Raises ValueError with a plain message."""
    try:
        idp = OneLogin_Saml2_IdPMetadataParser.parse(xml.strip())
    except Exception:  # noqa: BLE001 - any parse failure means the same thing to the admin
        raise ValueError("That is not SAML metadata Atlas can read. Paste the whole XML from ClassLink.") from None
    found = idp.get("idp", {})
    if not found.get("entityId") or not found.get("singleSignOnService", {}).get("url"):
        raise ValueError("The metadata has no sign-in address in it. Check it is ClassLink's IdP metadata.")
    if not (found.get("x509cert") or found.get("x509certMulti")):
        raise ValueError("The metadata has no signing certificate in it, so Atlas could not check sign-ins.")
    return idp


def _settings(request, config=None):
    values = sp_values(request)
    data = {
        "strict": True,
        "sp": {
            "entityId": values["entity_id"],
            "assertionConsumerService": {"url": values["acs_url"], "binding": OneLogin_Saml2_Constants.BINDING_HTTP_POST},
            "NameIDFormat": OneLogin_Saml2_Constants.NAMEID_UNSPECIFIED,
        },
        # ClassLink signs the response, the assertion or both; python3-saml
        # still refuses one with no valid signature at all.
        "security": {"wantAssertionsSigned": False, "wantMessagesSigned": False, "requestedAuthnContext": False,
                     "wantAttributeStatement": False},
    }
    if config is None:
        return OneLogin_Saml2_Settings(data, sp_validation_only=True)
    return OneLogin_Saml2_IdPMetadataParser.merge_settings(data, parse_metadata(config.metadata))


def sp_metadata(request):
    return _settings(request).get_sp_metadata()


def _auth(request, config):
    data = {
        "https": "on" if request.is_secure() else "off",
        "http_host": request.get_host(),
        "script_name": request.path,
        "get_data": request.GET.copy(),
        "post_data": request.POST.copy(),
    }
    return OneLogin_Saml2_Auth(data, _settings(request, config))


def login_url(request, config, next_url):
    return _auth(request, config).login(return_to=next_url)


def read_response(request, config):
    """Check ClassLink's signed answer. Returns (assertion id, expires, identity)."""
    auth = _auth(request, config)
    # No request id: sign-ins started from the ClassLink LaunchPad are unsolicited,
    # and ours come back cross-site without the session cookie anyway. Replays are
    # stopped by UsedAssertion instead.
    auth.process_response()
    if auth.get_errors() or not auth.is_authenticated():
        raise SignInRefused(f"ClassLink's answer could not be checked ({auth.get_last_error_reason() or 'unknown reason'}).")
    attributes = {key.lower(): values for key, values in auth.get_attributes().items()}

    def first(keys):
        for key in keys:
            if attributes.get(key):
                return attributes[key][0].strip()
        return ""

    name_id = (auth.get_nameid() or "").strip()
    email = first(EMAIL_KEYS) or (name_id if "@" in name_id else "")
    if not email:
        raise SignInRefused("ClassLink did not send an email address. Map Email in the ClassLink app's attributes.")
    identity = {"email": email.lower(), "first_name": first(FIRST_KEYS), "last_name": first(LAST_KEYS)}
    return auth.get_last_assertion_id(), auth.get_last_assertion_not_on_or_after(), identity


def find_or_create_user(identity, create):
    User = get_user_model()
    email = identity["email"]
    user = (
        User.objects.filter(email__iexact=email).order_by("pk").first()
        or User.objects.filter(username__iexact=email).first()
    )
    if user is None:
        if not create:
            raise SignInRefused("You do not have an Atlas account yet. Ask an Atlas administrator to add you.")
        # Starts turned off: an administrator approves it on Users before it can sign in.
        user = User(username=email, email=email, first_name=identity["first_name"], last_name=identity["last_name"],
                    is_active=False)
        user.set_unusable_password()  # ClassLink only, until an administrator sets one
        user.save()
        raise SignInRefused("Your Atlas account was requested. An Atlas administrator needs to approve it before you can sign in.")
    if not user.is_active:
        if user.last_login is None:
            raise SignInRefused("Your Atlas account is still waiting for an administrator to approve it.")
        raise SignInRefused("Your Atlas account is turned off. Ask an Atlas administrator.")
    return user
