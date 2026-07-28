import sys, urllib.request, urllib.error
sys.path.insert(0, '.')
from src.utils.cloud_sync import get_cloud_client
c = get_cloud_client()
req = urllib.request.Request('http://127.0.0.1:42040/api/v1/contacts?contact_type=friend', headers={'Authorization': 'Bearer ' + c.jwt_token})
try:
    with urllib.request.urlopen(req) as r:
        print(r.read())
except urllib.error.HTTPError as e:
    print(e.read().decode('utf-8'))
