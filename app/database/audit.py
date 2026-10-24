"""거래 변경 이력과 버전. 모든 SQL 쓰기 경로를 같은 트리거로 기록한다."""

FIELDS = ('type', 'stockName', 'stockCode', 'brokerAccount', 'subAccount', 'accountName',
          'currency', 'tradeType', 'price', 'quantity', 'rawDate', 'tradeDate', 'tradeSequence',
          'title', 'thoughts', 'tags', 'tradeClass', 'tradeStatus', 'realizedPnl',
          'realizedPnlSource', 'needsReview', 'reviewReason', 'date', 'fee', 'tax', 'exchange',
          'assetType', 'isHidden', 'attachedFileName', 'isSimulated', 'isSystem', 'confidence',
          'orderOrigin', 'source', 'orderId', 'originalOrderId', 'realizedPnlRate',
          'strategyScore', 'stopLossRate', 'executedAtUtc', 'brokerExecutionId')


def install_functions(conn):
    def origin():
        from flask import has_request_context, request
        if not has_request_context():
            return 'system'
        return 'bot' if request.path.startswith('/api/v1/') else 'web'
    conn.create_function('change_origin', 0, origin)


def install_triggers(conn):
    def snapshot(prefix):
        fields = ','.join(f"'{field}',{prefix}.{field}" for field in FIELDS)
        return f'json_object({fields})'
    changed = ' OR '.join(f'OLD.{field} IS NOT NEW.{field}' for field in (*FIELDS, 'attachedFile', 'attachedImage'))
    for action in ('insert', 'update', 'delete'):
        row = 'OLD' if action == 'delete' else 'NEW'
        before = 'NULL' if action == 'insert' else snapshot('OLD')
        after = 'NULL' if action == 'delete' else snapshot('NEW')
        condition = f'WHEN {row}.username IS NOT NULL'
        if action == 'update':
            condition += f' AND ({changed})'
        revision = ('UPDATE entries SET revision=last_insert_rowid() WHERE id=NEW.id;'
                    if action != 'delete' else '')
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS entries_audit_{action}
            AFTER {action.upper()} ON entries {condition} BEGIN
                INSERT INTO entry_changes(username,entry_id,action,origin,before_json,after_json)
                VALUES ({row}.username,{row}.id,'{action}',change_origin(),{before},{after});
                {revision}
            END''')
