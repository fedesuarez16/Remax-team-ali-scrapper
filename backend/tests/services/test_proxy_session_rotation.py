"""A 403 is a burnt exit IP, not "this zona has no listings".

Live, two consecutive ZonaProp URLs came back `403 Forbidden` and the portal
contributed nothing to a run where every other source worked (424 properties).
The Apify actor never had this problem because it rotates the proxy session on
every launch — its own log says `Browser launching with proxy session:
zp_71397`. We reused one session, so once an exit IP was flagged it stayed
flagged for the whole search.

Apify encodes the session in the proxy USERNAME:
`groups-RESIDENTIAL,session-<id>`. A different id is a different exit IP.
"""
from app.services.apify import _next_proxy_session, _proxy_with_session


class TestBuildingTheRotatedUrl:
    def test_it_adds_a_session_to_a_plain_username(self):
        out = _proxy_with_session(
            'http://groups-RESIDENTIAL:pw@proxy.apify.com:8000', 'abc123')
        assert out == 'http://groups-RESIDENTIAL,session-abc123:pw@proxy.apify.com:8000'

    def test_it_replaces_an_existing_session(self):
        """Appending a second `session-` would make the username invalid."""
        out = _proxy_with_session(
            'http://groups-RESIDENTIAL,session-old:pw@proxy.apify.com:8000', 'new1')
        assert 'session-old' not in out
        assert 'session-new1' in out

    def test_other_username_options_survive(self):
        out = _proxy_with_session(
            'http://groups-RESIDENTIAL,country-AR:pw@proxy.apify.com:8000', 'x1')
        assert 'groups-RESIDENTIAL' in out
        assert 'country-AR' in out
        assert 'session-x1' in out

    def test_the_password_is_untouched(self):
        out = _proxy_with_session('http://user:pw123@proxy.apify.com:8000', 'x1')
        assert out == 'http://user,session-x1:pw123@proxy.apify.com:8000'

    def test_no_proxy_configured_stays_no_proxy(self):
        assert _proxy_with_session('', 'x1') is None
        assert _proxy_with_session(None, 'x1') is None

    def test_a_proxy_without_credentials_is_left_alone(self):
        """Nothing to rotate — returning it unchanged beats corrupting it."""
        assert _proxy_with_session('http://proxy.local:3128', 'x1') == 'http://proxy.local:3128'


class TestSessionIds:
    def test_successive_ids_differ(self):
        assert _next_proxy_session() != _next_proxy_session()

    def test_the_id_is_alphanumeric(self):
        """Apify rejects a session id carrying punctuation."""
        assert _next_proxy_session().isalnum()
