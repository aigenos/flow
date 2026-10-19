---
name: table-booking
description: >-
  Book a restaurant table. Use when the user wants to book a table, reserve a
  spot, or make a dinner or lunch reservation.
license: Apache-2.0

# A pick from a list, two answers with a shape, a rule a lookup decides, a
# question only for large parties, and one tool that changes something.
steps:
  - ask: { restaurant_id: "Which restaurant?" }
    from: list_restaurants.restaurants
  - ask: { day: "Which day? (YYYY-MM-DD)" }
    pattern: '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  - ask: { time: "What time? (HH:MM)" }
    pattern: '^([01][0-9]|2[0-3]):[0-5][0-9]$'
    # Judged once the party size is known (the lookup needs it); a full slot asks for another time.
    check: { check_table.available: "That time is fully booked. Pick another time." }
  - ask: { party_size: "For how many people?" }
    type: whole number
    check:
      party_size >= 1: "A table is for at least one person."
      party_size <= 20: "For more than 20, please call the restaurant."
  - ask: { card_last4: "Parties over 8 hold the table with a card. What are its last 4 digits?" }
    when: party_size > 8
    pattern: '^[0-9]{4}$'
  - say: "{{check_table.summary}}"
    title: Your table
  - confirm: Book this table?
  - do: book_table
  - say: "Booked. Reference {{book_table.reference}}."
---

Help the user book a table. The steps above decide what is asked, which rule
applies and when the table is booked; this text is only context.
