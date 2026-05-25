import datetime

def health_check():
    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "status": "ok",
        "version": "1.0"
    }

if __name__ == "__main__":
    print(health_check())