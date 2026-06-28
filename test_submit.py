import json
from app import app

inputs = [
    ('clearly_ai', 'Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits of AI are numerous, it is equally essential to consider the ethical implications. Furthermore, stakeholders across various sectors must collaborate to ensure responsible deployment.'),
    ('clearly_human', 'ok so i finally tried that new ramen place downtown and honestly? the broth was fine but they put WAY too much sodium in it and it was underwhelming. the rest of my friend got the spicy version and said it was better.'),
    ('borderline_formal', 'The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations.'),
    ('borderline_edited_ai', "I've been thinking a lot about remote work lately. There are genuine tradeoffs – flexibility and no commute on one side, isolation and blurred work-life boundaries on the other. Studies show productivity varies widely by individual and role type.")
]

with app.test_client() as client:
    created_ids = []
    for name, text in inputs:
        res = client.post('/submit', json={'text': text, 'creator_id': 'tester'})
        created_ids.append(res.json['content_id'])
        print('===', name, '===')
        print(json.dumps(res.json, indent=2))
        print()

    if created_ids:
        appeal_res = client.post('/appeal', json={
            'content_id': created_ids[0],
            'creator_id': 'tester',
            'reason': 'Please review; I believe this is human-generated.'
        })
        print('=== APPEAL RESPONSE ===')
        print(json.dumps(appeal_res.json, indent=2))
        print()

    res = client.get('/log')
    print('=== LOG ENTRIES ===')
    print(json.dumps(res.json, indent=2))
