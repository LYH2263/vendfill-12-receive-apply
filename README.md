# VendFill 售货机补货

按货道容量、库存与在途量计算缺口，生成不超缺口、非负的补货单。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4800 |
| API | http://localhost:9800 |
| API 文档 | http://localhost:9800/docs |
| Postgres | localhost:5449 |

健康检查：`GET http://localhost:9800/api/health`

## 使用说明

1. 在「点位」「货道」查看售货机布局与库存。
2. 在「销量」了解近期出货。
3. 打开「补货单」按缺口生成建议补货量。
4. 货到后在「补货单」点「核销到货」：按行把补量加入货道库存、在途扣减（不足扣到 0，不为负），单据标记已核销；货道、汇总、满仓同一跳变。已核销/已作废/货道缺失一律拒绝且全部回退，重复核销不漂移。不需要的单可「作废」。
5. 在「满仓」「汇总」查看已满货道与补货合计（均按货道现值实时计算）。

## 开发与测试

```bash
docker compose exec api pytest -q
```
