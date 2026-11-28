"""Write a local, repeatable audit from a private lifecycle checkpoint.

Never uploads Telegram receipts, chat identifiers or credentials.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import signal_review


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('checkpoint',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rows=json.loads(args.checkpoint.read_text(encoding='utf-8'))['rows']
    rows=sorted(rows,key=lambda r:(r['sent_at'],r['id']))
    first={}
    for row in rows:first.setdefault(signal_review.opportunity(row),row)
    summary=signal_review.summarize(rows)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    msk=timezone(timedelta(hours=3))
    fmt=lambda at:datetime.fromtimestamp(at,msk).strftime('%d.%m.%Y %H:%M:%S МСК')
    rate=lambda v:'—' if v is None else f'{v*100:.1f}%'
    s=summary['first_opportunities']; compatible=summary['compatible_first_opportunities']
    lines=['# Аудит сигналов Telegram', '',f'Замороженная выборка: {fmt(rows[0]["sent_at"])} — {fmt(rows[-1]["sent_at"])}.',
        '',f'Получено {len(rows)} записей по {summary["all_receipts"]["events"]} матчам. Из них {summary["updates"]} — обновления уже наблюдавшейся цели.',
        f'Первые сигналы по уникальной цели: {s["records"]}; подтверждённые выигрыши {s["states"].get("won",0)}, проигрыши {s["states"].get("lost",0)}, непроверенные {s["states"].get("pending",0)}.',
        f'Доля выигрышей среди подтверждённых первых сигналов: {rate(s["win_rate"])}. Средняя исходная оценка модели: {rate(s["mean_model_probability"])}. Brier: {s["brier"]}.',
        f'Только совместимые рынки: {compatible["states"].get("won",0)} выигрышей / {compatible["states"].get("lost",0)} проигрышей; {rate(compatible["win_rate"])}.',
        '', 'Это описательный разбор выбранных уведомлений. Несколько целей одного матча связаны. Непроверенные исходы не считаются проигрышами. Для несовместимого рынка с ОТ исход по основному времени не является подтверждением расчёта ставки.',
        '', '## Подтверждённые проблемы', '',
        '1. Модельные оценки переоценивают фактическую частоту выигрышей. Их нельзя использовать как проверенный шанс.',
        '2. Обновления одной линии не являются новыми независимыми прогнозами. Оценивать качество нужно по первому сигналу на цель и отдельно по версиям политики.',
        '3. Часть уведомлений сравнивает прогноз основного времени с рынком, включающим ОТ. Такие записи исключены из совместимой группы.',
        '4. Бот раньше ставил уведомление в очередь до расчёта обучаемой модели; её противоположный прогноз не влиял на отправку.',
        '5. Старые квитанции не сохраняли полный снимок входа: нет достаточных данных для достоверного объяснения проигрыша штрафными, владениями или внезапным изменением темпа.',
        '', '## Изменения', '',
        'Отправка выполняется после расчёта обучаемой модели. Явное разногласие направлений на одной линии блокирует экспериментальное уведомление; числовые прогнозы остаются на сайте. Отсутствие дополнительного расчёта помечается отдельно и не выдаётся за согласие.',
        'Новые квитанции сохраняют счёт, часы, результаты периодов, доступную статистику и версии/результаты моделей. Добавлен API /api/signals/quality с первым сигналом на цель, отдельными обновлениями, пропусками и версиями политики.',
        'Новые веса по известным проигрышам не подбирались. Эффект дополнительной проверки необходимо измерять на последующих матчах; роста выигрышей пока не установлено.',
        '', '## Результаты по целям', '', '| Цель | Выигрыши | Проигрыши | Не подтверждено | Доля выигрышей |', '|---|---:|---:|---:|---:|']
    for target,group in summary['by_target'].items():
        states=group['states'];lines.append(f'| {target} | {states.get("won",0)} | {states.get("lost",0)} | {states.get("pending",0)} | {rate(group["win_rate"])} |')
    lines+=['', '## Все первые сигналы на цель', '', '| Время МСК | Матч | Цель | Направление / линия | Оценка модели | Итог цели | Исход | Основание |', '|---|---|---|---|---:|---:|---|---|']
    labels={'won':'Зашло','lost':'Не зашло','push':'Возврат','pending':'Не подтверждено','invalid_result':'Несогласованный итог'}
    for row in first.values():
        payload=row['payload'];signal=payload['signal'];result=row.get('result') or {}
        target=signal['target']+(f' {signal.get("period")}' if signal['target']!='match' else '')
        p=signal.get('probability');evidence=result.get('evidence') or {}
        source=evidence.get('source_url','')
        basis=f'[{evidence.get("basis","источник")}]({source})' if source else '—'
        lines.append(f'| {fmt(row["sent_at"])} | {payload["teams"].replace("|","/")} | {target} | {signal["direction"]} {signal["line"]:g} | {rate(p)} | {result.get("total","—")} | {labels[signal_review.verified_state(row)]} | {basis} |')
    (args.output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'receipts':len(rows),'first':s,'report':str(args.output/'report.md')},ensure_ascii=True))


if __name__=='__main__':main()
