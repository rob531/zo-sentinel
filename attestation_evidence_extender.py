import logging
from logging.handlers import RotatingFileHandler
from logging.config import dictConfig

logging_config = {
    'version': 1,
    'formatters': {
        'default': {
            'format': '[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'default'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': './logs/z sentinel.log',
            'maxBytes': 1024 * 1024 * 100,  # 100 MB
            'backupCount': 20,
            'formatter': 'default'
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console', 'file']
    }
}

dictConfig(logging_config)

def run():
    logger = logging.getLogger(__name__)

    if __name__ == '__main__':
        logger.info('Starting ZO-SENTINEL')
        # Initialize variables and databases here
        # ...