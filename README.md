# Accountable

Know who you're paying. Accountable takes your (mock) Capital One transactions from the Nessie API,
finds the merchants you buy from, researches their ethical record, scores them against **your**
values, maps how their products are made and reach you, and suggests better-aligned alternatives.

## Inspiration
In today's age, all of our daily products are made in companies thousands of miles away. Before hyper-globalization, everyone's daily products were made locally and you knew where your things were sourced from. We wanted to bring users an awareness of their purchases and a higher level of connectedness to the places where your things come from.

## What it does
The website pulls companies from your purchase history, and places their suppliers on a map of where your objects are sourced from. Wikipedia articles are linked to each location as interesting facts in order to connect the user to the people from where you bought your products. 

The website also integrates your personalized values through a fillable form. Any of the companies you spend from are analyzed for values such as labor and workers, environment, animal welfare, local community through reliable public database sources.  A summary is then provided on how aligned you are with your values compared to the companies that you purchase from. 

## How we built it
This project was built with a FastAPI backend and a React frontend.

We used Nessie from Capital One to mock our financial data, which includes customers, merchants, accounts, transactions, and more. Merchant's that have been purchased from are analyzed using Gemini through the Gemini API, which researches relevant ethical information about the company. The findings of this research is validated and weighted on your personal values, which allows our application to tell you if the specified company aligns with your values or not. Public databases such as Department of Labor, OSHA, NRLB, EPA were referenced for their information on specific companies to see if there were any lawsuits or violations. The values are then weighed using Gemini and then compared to your own inputed values.

Company manufacturer data was sourced from ImportYeti API which provides us supplier sources through public freight shipment info. The Company's manufacturer's precise address is then converted into coordinates and plugged into Wikipedia's free Geosearch API, which provides nearby wikipedia articles on any given coordinate.


**How to install**

## Run it locally

Runs on mock Nessie data. You need a **MongoDB Atlas** connection string

**Backend** (Python 3.11+)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then set MONGODB_URI in .env
uvicorn app.main:app --reload --port 8000
```
On first start the backend loads the mock customer into Atlas. Re-sync any time with
`python -m scripts.sync`.

The API only serves the configured customer. Set `ALLOW_CUSTOMER_OVERRIDE=true` locally if you
need `?customer_id=` or `/api/customers`; keep it `false` on any public deploy.

API docs at http://localhost:8000/docs

Open http://localhost:5173

**Frontend** (Node 20+), in a second terminal
```bash
cd frontend
npm install
npm run dev
```
