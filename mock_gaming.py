import socket
import time
import threading

def mock_game_traffic():
    """Mocks gaming traffic by blasting small UDP packets continuously."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_address = ('8.8.8.8', 53) # Arbitrary safe endpoint (Google DNS)
    
    print("Mock Game Traffic started. Firing UDP packets...")
    print("Keep this terminal open, and watch the dashboard in the other terminal switch to 'Gaming' or 'Congested'!")
    
    try:
        while True:
            # Send a tiny 64-byte payload representative of gaming tick data
            sock.sendto(b'X' * 64, server_address)
            time.sleep(0.01) # Fire 100 times a second
    except KeyboardInterrupt:
        print("\nStopped mock gaming traffic.")
    finally:
        sock.close()

if __name__ == "__main__":
    # Spin up 50 fake connections to trigger the >50 UDP sockets heuristic
    threads = []
    print("Simulating 50+ active UDP connections...")
    for _ in range(60):
        t = threading.Thread(target=mock_game_traffic, daemon=True)
        t.start()
        threads.append(t)
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Done.")
