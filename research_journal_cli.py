"""Local, owner-run journal export/confirmation. Never sends messages or bets."""
import argparse
import json
import forecast_journal


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    export = sub.add_parser('export')
    export.add_argument('output')
    confirm = sub.add_parser('confirm')
    confirm.add_argument('forecast_id')
    confirm.add_argument('final_json')
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(forecast_journal.status(), ensure_ascii=False))
    elif args.command == 'export':
        with open(args.output, 'w', encoding='utf-8') as stream:
            for row in forecast_journal.export_rows():
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    else:
        with open(args.final_json, encoding='utf-8') as stream:
            result = forecast_journal.confirm(args.forecast_id, json.load(stream))
        print(json.dumps(result, ensure_ascii=False))
