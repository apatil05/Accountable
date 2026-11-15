# Accountable

Know who you're paying. Accountable takes your (mock) Capital One transactions from the Nessie API,
finds the merchants you buy from, researches their ethical record, scores them against **your**
values, and maps where their products are made and how they reach you.

Live at **accountable.select**.

## Inspiration

In today's age, all of our daily products are made in companies thousands of miles away. Before
hyper-globalization, everyone's daily products were made locally, and you knew where your things
were sourced from. We wanted to bring users awareness of their purchases and a higher level of
connectedness to the places where their things come from.

## What it does

The website integrates your personalized values through sliders determined by the user. Any of the
companies you spend with are analyzed based on how they value labor and workers, the environment,
animal welfare, local community, and privacy. A summary is then provided on how aligned you are
with your values compared to the companies that you purchase from.

The website also pulls companies from your purchase history and places their suppliers on a map of
where your objects are sourced from. Wikipedia articles are linked to each location as interesting
facts in order to connect the user to the people from where you bought your products.

## How we built it

This project was built with a FastAPI backend and a React frontend.

We used Nessie from Capital One to fetch our financial data, which includes customers, merchants,
accounts, transactions, and more. Merchants that have been purchased from are analyzed using the
Gemini API. Gemini researches relevant ethical information about the company, forming claims about
the company and aggregating sources that relate to said claims. This is handed off to another call
to Gemini (the judge), which determines if the claims are relevant based on the sources provided.
It will tweak the claims based on its judgment, create a verdict, and return a quote supporting the
claim. The code then checks the verdict and checks if the company name and quote are present in the
source. Once this is finished, the verdict and source are validated. Claims that failed this
validation are deleted, and the ones that survive are given a score tiered on severity, source, and
recency. Finally, these scores are weighted on the user's values to determine if the user "aligns"
with the company.

Public databases such as the Department of Labor, OSHA, NLRB, and EPA were referenced for their
information on specific companies to see if there were any lawsuits or violations.

Company manufacturer data was sourced from an API called ImportYeti, which provides us supplier
sources through public freight shipment info. The company's manufacturer's precise address is then
converted into coordinates and plugged into Wikipedia's free Geosearch API, which provides nearby
Wikipedia articles on any given coordinate.

## Run it locally

Accountable runs on mock Nessie data by default, so you only need a MongoDB database. Nessie and
Gemini keys are optional. Without a Gemini key, the app serves saved research and sample scores;
page loads never call Gemini.

### Prerequisites

- Python 3.11+ and Node 20+
- A [MongoDB Atlas](https://www.mongodb.com/atlas) cluster (the free M0 tier is enough)

To create one: make a free M0 cluster, add a database user with read/write access, allow your IP
under Network Access, then copy the connection string from Connect -> Drivers. Optionally install
[MongoDB Compass](https://www.mongodb.com/products/tools/compass) and paste the same string to
browse the data.

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then set MONGODB_URI in .env
uvicorn app.main:app --reload --port 8000
```

On first start the backend loads the mock customer into your database. API docs are at
http://localhost:8000/docs.

Useful settings in `backend/.env`:

| Variable | Purpose |
| --- | --- |
| `MONGODB_URI` | Required. Your Atlas connection string. Never commit it. |
| `GEMINI_API_KEY` | Enables research. Without it, only saved research is shown. |
| `NESSIE_API_KEY` | Use the real Nessie sandbox instead of mock data (with `USE_MOCK_DATA=false`). |
| `IMPORTYETI_API_KEY`, `DOL_API_KEY` | Supplier and labor data sources. |
| `ADMIN_TOKEN` | Required as the `X-Admin-Token` header for `POST /api/sync`. Empty disables the route. |
| `ALLOW_CUSTOMER_OVERRIDE` | Enables `?customer_id=` and `/api/customers`. Keep `false` on public deployments. |
| `RESEARCH_DAILY_CAP` | Most companies researched on demand per day. |

### Data and research scripts

Run from `backend/`:

```bash
python -m scripts.sync                    # pull the customer from Nessie (or mock) into MongoDB
python -m scripts.seed_users              # create demo accounts with sample purchases
python -m scripts.prewarm                 # research the customer's brands with Gemini and cache the results
python -m scripts.prewarm --only blurbs   # rewrite verdict explanations after a values change
python -m scripts.prewarm_suppliers       # suppliers and nearby Wikipedia places for the map
python -m scripts.load_data all           # DOL, OSHA, NLRB, Climate TRACE and company reports
python -m scripts.eval_validation         # precision and recall of claim validation vs hand labels
```

Research is cached, so each company is researched once. Companies without saved research are
researched on demand when their page is opened, up to the daily cap. A GitHub Actions workflow
(`.github/workflows/nightly-data.yml`) refreshes the outside datasets nightly.

### Tests

From `backend/`, run `pytest`. Tests that need a database use a throwaway `accountable_test_*`
database on your cluster and are skipped when `MONGODB_URI` is unset.

### Frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend. `npm run build` type-checks and
builds, and `npm test` runs the frontend tests.
