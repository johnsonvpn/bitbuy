import base64
from struct import pack

pem = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA2ACg7sTgGMEmNMks+wKfP26pXCSirRMegzN+CPXQoFkVdmSr
1lvJxu0fV2e/iu10UyzhX4OTF6vAsviuiW0DdAX3+fRa6Ntm5ez7PUxZAC69LzYA
9+TadSuUAzYeyslbmr9HdjMQ7+71Ri5nvFxYTyUNg0s52L+5aTLSjgWJ+A0QLFPL
EgDzDDGdUPXqAu+PRNe/7oBOMB9sfbkaza/DA/dOGH5Lh55yYiubA9YFT6+pc0ZD
MoPPqSkWVepcscgU/fEtxQmK+yLKKFh1DVOJ+RpUaCnR77yxeqWZK7ZsgF6u+16z
ili+x9KDb2SjsFa6W4DQ8heW6Jt6RpNGPMyUSwIDAQABAoIBAH0eUBo+vDhamZD1
/zIe4LPTnBKdvgVXD9Ob3iO+j+xx7ba4tbjjTkwGSqNMm20UAs+zLZEwG+IYQPTq
i99a4Ccy1eNZoddET6Rb02Q8d0ldyYQxWfo9/DTm20PI86kvXfqTVgenqOXymuze
MISxUQ33Po6Q4p8k33eKUuClYLfneJMKk8YJs1eM2zQvzSxPK11fBNSv+ve00Ka5
cDWmqliO94IQztthQ1jWaXWJOALb11mGS3mem9gpBXOCpe9yv1oxGhJuq2IkBVLB
9PXtMFuTYV/kBAahq8wfIjC7S6YO1i45S97hBWRe9BMceIo9FeF72dYd+AApuHxb
R7j1gHECgYEA9NTqf67ehqG4zbKr/OBpCpIZICk4P4XpWG8boJVbbvr6YM8FBvH+
3/aDazKbCobe8C37MqgX3W74ELNPy4L+rDouWlHiCe/V2Ul0UugHEG8eRjaYq0N6
fSfmhZhI3h7OW+cQYmMUSLNvfxypspXyET5qcpev3fd3AXTHAcf9VlUCgYEA4dsN
RBmbH9c+n0zcJSICuVJPCxqAXlllx7IiDM4Egt4oSadXn+sXOako/L3inV8r2ul2
IsoYErZ02gOaU/2l/I0AA/dgpoT/0yqqu9+OOQnG/ZGgcjJZoPNHJXzeDNPkKL/a
UC5egM5Yd4e3uIGSlWevzZ9dy4o101hLJ3iCoB8CgYEAhZZzSS3yL0Won1wBKd6M
kf77hKfZEBgSJXWifnFgTWLWIOJ0XMDIEA3i0SfwnsLOfACq0o4TT3hQFFSyklms
ee7ZAeEx07gwV/oTZXVC/X2T6D27+Z69A/M0feqEv+XRNSYTs4taMvztNk8+bLoS
NcR3soT/qb5wCbRdLaSgn20CgYBRJRx9DR4YsILFRR4LEU8dOh9ABAN+4muY9b0a
EKK6Sgr7e24V/KbZhpc9RxO2Ks1c12gkU6uWfYs6EPVPm+AY/qe0xRoqebpYKgox
eb5la8fcroeQv9pH41/6bgRxY2ej5FoRWLeHW5uZRl+RoKwDlb8qB0nxqPRxvFU0
Fy4HTQKBgF00CDvtu4p9oPxKWOhf07mpX/ikuc57w/60T8YvIUS37nRhxhO+oBLY
I0wXRgyhZZCl3Y1+hvE6nukwNSZd/HwRmNhZKI6ThAdUTQCfRRMdwtW9MyxOGAnU
nPb03Va/HlDHzsR9Dc4AyNfThw6ett7VDUaWf8BSeM4WTA0YrLN6
-----END RSA PRIVATE KEY-----"""

lines = pem.strip().split('\n')
data = base64.b64decode(''.join(lines[1:-1]))

def read_length(data, offset):
    if data[offset] < 0x80:
        return data[offset], offset + 1
    else:
        len_bytes = data[offset] & 0x7F
        return int.from_bytes(data[offset+1:offset+1+len_bytes], 'big'), offset + 1 + len_bytes

def read_element(data, offset):
    tag = data[offset]
    length, offset = read_length(data, offset + 1)
    value = data[offset:offset+length]
    return tag, value, offset + length

offset = 0
elements = []
while offset < len(data):
    tag, value, offset = read_element(data, offset)
    elements.append((tag, value))

e = int.from_bytes(elements[2][1], 'big')
n = int.from_bytes(elements[1][1], 'big')

e_bytes = e.to_bytes(3, 'big')
n_bytes = n.to_bytes((n.bit_length() + 7) // 8, 'big')

encoded = pack('>I', len(e_bytes)) + e_bytes + pack('>I', len(n_bytes)) + n_bytes
public_key = f"ssh-rsa {base64.b64encode(encoded).decode('ascii')}"

print("Public key for jpbian.pem:")
print(public_key)

with open('/Users/johnsontang/work/bitbuy/newst/jpbian.pub', 'w') as f:
    f.write(public_key)

print("\nSaved to: /Users/johnsontang/work/bitbuy/newst/jpbian.pub")
