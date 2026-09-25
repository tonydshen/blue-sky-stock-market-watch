import requests

url = "https://localhost:5000/v1/api/iserver/auth/ssodh/init"
payload = {}
headers = {"Content-Type": "application/json"}

response = requests.post(url, json=payload, headers=headers, verify=False)

