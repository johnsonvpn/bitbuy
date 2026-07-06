import random
import math

def is_prime(n, k=40):
    if n < 2:
        return False
    if n == 2:
        return True
    if n == 3:
        return True
    if n % 2 == 0:
        return False
    
    r, s = 0, n - 1
    while s % 2 == 0:
        r += 1
        s //= 2
    
    for _ in range(k):
        a = random.randrange(2, n - 1)
        x = pow(a, s, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True

def generate_prime(bits):
    while True:
        n = random.getrandbits(bits)
        n |= (1 << (bits - 1)) | 1
        if is_prime(n):
            return n

def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    gcd, x1, y1 = extended_gcd(b % a, a)
    x = y1 - (b // a) * x1
    y = x1
    return gcd, x, y

def mod_inverse(e, phi):
    gcd, x, _ = extended_gcd(e % phi, phi)
    if gcd != 1:
        raise ValueError("Modular inverse doesn't exist")
    return (x % phi + phi) % phi

def generate_rsa_key(bits=2048):
    p = generate_prime(bits // 2)
    q = generate_prime(bits // 2)
    while p == q:
        q = generate_prime(bits // 2)
    
    n = p * q
    phi = (p - 1) * (q - 1)
    
    e = 65537
    while math.gcd(e, phi) != 1:
        e = random.randrange(3, phi, 2)
    
    d = mod_inverse(e, phi)
    
    return {
        'n': n,
        'e': e,
        'd': d,
        'p': p,
        'q': q
    }

def int_to_bytes(n):
    byte_length = (n.bit_length() + 7) // 8
    return n.to_bytes(byte_length, 'big')

def to_pem_private(key):
    n = key['n']
    e = key['e']
    d = key['d']
    p = key['p']
    q = key['q']
    
    def i2osp(x, length):
        return x.to_bytes(length, 'big')
    
    version = b'\x00'
    n_bytes = i2osp(n, (n.bit_length() + 7) // 8)
    e_bytes = i2osp(e, 3)
    d_bytes = i2osp(d, (d.bit_length() + 7) // 8)
    p_bytes = i2osp(p, (p.bit_length() + 7) // 8)
    q_bytes = i2osp(q, (q.bit_length() + 7) // 8)
    dmp1 = i2osp(d % (p - 1), (p.bit_length() + 7) // 8)
    dmq1 = i2osp(d % (q - 1), (q.bit_length() + 7) // 8)
    iqmp = i2osp(mod_inverse(q, p), (p.bit_length() + 7) // 8)
    
    def encode_length(data):
        length = len(data)
        if length < 128:
            return bytes([length])
        length_bytes = length.to_bytes((length.bit_length() + 7) // 8, 'big')
        return bytes([0x80 | len(length_bytes)]) + length_bytes
    
    def encode_element(tag, data):
        return bytes([tag]) + encode_length(data) + data
    
    elements = [
        encode_element(0x02, version),
        encode_element(0x02, n_bytes),
        encode_element(0x02, e_bytes),
        encode_element(0x02, d_bytes),
        encode_element(0x02, p_bytes),
        encode_element(0x02, q_bytes),
        encode_element(0x02, dmp1),
        encode_element(0x02, dmq1),
        encode_element(0x02, iqmp)
    ]
    
    der = bytes([0x30]) + encode_length(b''.join(elements)) + b''.join(elements)
    
    import base64
    pem = "-----BEGIN RSA PRIVATE KEY-----\n"
    pem += base64.b64encode(der).decode('ascii')
    pem += "\n-----END RSA PRIVATE KEY-----"
    return pem

def to_openssh_public(key):
    n = key['n']
    e = key['e']
    
    import base64
    e_bytes = e.to_bytes(3, 'big')
    n_bytes = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    
    from struct import pack
    encoded = pack('>I', len(e_bytes)) + e_bytes + pack('>I', len(n_bytes)) + n_bytes
    return f"ssh-rsa {base64.b64encode(encoded).decode('ascii')}"

key = generate_rsa_key(2048)

private_pem = to_pem_private(key)
public_key = to_openssh_public(key)

with open('/Users/johnsontang/work/bitbuy/newst/aws_key', 'w') as f:
    f.write(private_pem)

with open('/Users/johnsontang/work/bitbuy/newst/aws_key.pub', 'w') as f:
    f.write(public_key)

print("Private key saved to: /Users/johnsontang/work/bitbuy/newst/aws_key")
print("Public key saved to: /Users/johnsontang/work/bitbuy/newst/aws_key.pub")
print("\nPublic key content (add this to AWS EC2):")
print(public_key)
