"""Demande une consumer key OVH limitée aux zones DNS :
   docker compose run --rm armadok python -m app.tools.ovh_consumer_key
(OVH_ENDPOINT, OVH_APPLICATION_KEY et OVH_APPLICATION_SECRET doivent être définis)"""
import ovh

from app.config import settings

client = ovh.Client(endpoint=settings.ovh_endpoint, application_key=settings.ovh_app_key,
                    application_secret=settings.ovh_app_secret)
req = client.new_consumer_key_request()
req.add_recursive_rules(ovh.API_READ_WRITE, "/domain/zone")
res = req.request()
print("1) Validez la clé ici (durée : « Illimitée ») :\n  ", res["validationUrl"])
print("\n2) Puis mettez dans .env :\n   OVH_CONSUMER_KEY=" + res["consumerKey"])
