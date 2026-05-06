import redis

def test_redis(host, port, password=None):
    try:
        r = redis.Redis(host=host, port=port, password=password, socket_timeout=2)
        r.ping()
        print(f"Successfully connected to Redis at {host}:{port} (password={password is not None})")
        return True
    except Exception as e:
        print(f"Failed to connect to Redis at {host}:{port} (password={password is not None}): {e}")
        return False

print("Testing Redis connections...")
test_redis('localhost', 6379, 'redispw')
test_redis('localhost', 6379, None)
test_redis('localhost', 32768, 'redispw')
