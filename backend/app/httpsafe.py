"""Appels HTTP sortants : on ne suit jamais une redirection (les identifiants et jetons ne doivent pas partir ailleurs)."""
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
