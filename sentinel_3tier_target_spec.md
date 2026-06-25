# Example of a data consumer
def consume_data(source):
    data = fetch_data(source)
    return data

# Example of a scoring function
def calculate_score(data):
    score = 0
    for item in data:
        score += item['value']
    return score / len(data)