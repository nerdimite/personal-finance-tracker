# Categories

Source of truth for expense and income accounts. `make sync-categories` upserts these into Postgres and never deletes: a category removed from this file is marked inactive.

`income` is the income tree. Every other top-level name is an expense. Indent with two spaces. `name: a, b` declares children. A comma-separated line is a list of siblings. Text after `#` is a comment.

`expense:uncategorized` and `equity:opening` are seeded. They are not part of this tree.

Trip spend and spend with a person cut across this tree. Link a transaction to a trip, or set `person_id` on a posting. "Food, dining out, on the Kodai trip, with girlfriend" is one posting and shows up in all three views.

housing
  rent                      # includes maintenance (it's part of the rent)
  utilities: electricity, water, gas, internet, mobile, furniture_rental   # furniture_rental = Rentomojo, CityFurnish
  home_services: maid, urban_company, repairs   # urban_company = any Urban Company booking (cleaning, pest control, repairs); repairs = non-UC repairs
  household_supplies: kitchen, cleaning_supplies, toiletries_bathroom
  furniture_appliances      # buying, not renting
food
  groceries                 # for cooking at home
  ordered_in                # Swiggy/Zomato delivery
  dining_out
  snacks_beverages          # own snacks, chai, coffee
  alcohol
  quick_commerce            # fallback only: a Blinkit/Zepto/Instamart order whose items are unknown
transport
  local: cab, auto, bike_taxi, metro_bus, fuel, parking_tolls
  intercity: flights, trains, bus
  vehicle: service
shopping
  clothing_footwear, electronics, personal_care, online_misc
health
  doctor, pharmacy, tests, fitness_gym
insurance
  health, term_life, vehicle
entertainment
  movies, concerts_events, ott, subscriptions, games, hobbies   # ott = Netflix, Prime, Hotstar etc.; subscriptions = other non-OTT subscriptions
relationships
  partner                   # gifts/experiences not covered elsewhere
  gifts
  family_support
  social_outings
travel                      # trip-level costs not in other buckets (stay, activities)
  stay, activities, misc
learning
  courses, books
finance
  bank_fees, cc_fees_interest, taxes
work
  reimbursable              # tracked as receivable from employer
misc
  delivery_fees             # delivery / handling / platform fees on orders
  other
income
  salary, interest, dividends, refunds_cashback, gifts_received, other
