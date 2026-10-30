Hello, we are the Visionary Devs.

We created this as part of SIH 2026.

# Introduction

The problem we are facing is the conflict between essential railway maintenance work and the limited operational windows available on busy corridors. Multiple departments such as TMS, SMMS, and TRD often request possession blocks at the same time, while passenger and freight trains impose strict safety constraints, limited headways, and high-priority service requirements. This creates a complex scheduling challenge where we must balance maintenance efficiency, safety, and continuous train operations.

We have used a Lagrangian relaxation model to decompose the corridor scheduling problem into smaller, tractable subproblems while preserving operational feasibility. The model relaxes capacity and compatibility constraints, updates dual multipliers iteratively, and then combines those results with a CP-SAT-based optimization framework to assign maintenance windows efficiently. By analyzing train traffic criticality, prioritizing urgent work, and bundling compatible tasks into joint shadow blocks, the system reduces conflicts, improves utilization, and helps railway controllers allocate safe possession windows more intelligently.

# How to use

1. Clone the repository to your local machine.
2. Create and activate a virtual environment.
3. Install the project dependencies:

   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   For Windows:

   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt

4. Apply database migrations:

   python manage.py migrate

5. Seed the database with sample railway corridor data:

   python manage.py seed_data

   or

   python manage.py seed_railway_data

6. Run the optimization engine to generate railway block schedules:

   python manage.py optimize_schedule

7. Start the Django development server:

   python manage.py runserver

8. Open the application in your browser:

   http://127.0.0.1:8000/

9. Use the dashboard to view the master schedule, department-specific portals, and generated maintenance blocks.

This project is designed for railway operations planning, maintenance coordination, and AI-assisted block scheduling in a realistic Indian Railways context.
