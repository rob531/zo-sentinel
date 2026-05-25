import re
from enum import Enum

class Complexity(Enum):
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'

def validate_directive(d: dict) -> tuple[bool, list]:
    required_fields = ['task', 'handler', 'output_file', 'complexity', 'description']
    if not all(field in d for field in required_fields):
        return False, [f'Missing required field: {field}' for field in required_fields]

    complexity_map = {
        'high': Complexity.HIGH,
        'medium': Complexity.MEDIUM,
        'low': Complexity.LOW
    }
    if d['complexity'] not in complexity_map:
        return False, ['Invalid complexity']

    handler_map = {
        'generate_file': True,
        'write_raw': True,
        'run_script': True
    }
    if d['handler'] not in handler_map:
        return False, [f'Invalid handler: {d["handler"]}']

    output_extension_pattern = r'\.(py|sh|sql)$'
    if not re.match(output_extension_pattern, d['output_file']):
        return False, [f'Invalid output file extension']

    description_length = len(d['description'])
    if description_length <= 50:
        return False, [f'Description too short: {description_length} chars']

    return True, []

def validate_task(task: str) -> bool:
    task_pattern = r'^[a-zA-Z0-9_]+$'
    if not re.match(task_pattern, task):
        return False
    return True

def validate_handler(handler: str) -> bool:
    handler_map = {
        'generate_file': True,
        'write_raw': True,
        'run_script': True
    }
    return handler in handler_map

def validate_complexity(complexity: str) -> bool:
    complexity_map = {
        'high': Complexity.HIGH,
        'medium': Complexity.MEDIUM,
        'low': Complexity.LOW
    }
    return complexity in complexity_map

def validate_description(description: str) -> bool:
    description_length = len(description)
    return description_length > 50

def validate_directive_validator(d: dict) -> tuple[bool, list]:
    errors = []
    if not validate_task(d['task']):
        errors.append('Invalid task')
    if not validate_handler(d['handler']):
        errors.append(f'Invalid handler: {d["handler"]}')
    if not validate_complexity(d['complexity']):
        errors.append(f'Invalid complexity: {d["complexity"]}')
    if not validate_description(d['description']):
        errors.append('Description too short')
    return validate_directive(d), errors