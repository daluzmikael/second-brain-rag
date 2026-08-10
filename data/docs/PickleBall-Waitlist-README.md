
# Pickleball Waitlist (FIFO Queue Using `heapq`)

A small Python program that models a pickleball paddle waitlist using a priority queue. Players are added to a queue and assigned courts in first-in, first-out *(FIFO)* order.

This project is a clean example of how to simulate a queue using `heapq` by pairing each entry with an incrementing counter.

---

## What It Does

The `PickleballWaitlist` class supports:

- **Add a player** to the waitlist
- **Peek** at who’s next (without removing them)
- **Assign a court** to the next player (removes them from the queue)
- **Print** the entire waitlist in order

---

## How FIFO Works Here

Python’s `heapq` is a min-heap, so we push tuples like:  
(counter, player_name)

Since `counter` increases each time someone joins, the smallest counter always pops first → FIFO ordering is preserved.

---

## Requirements

- Python 3.x  
(Standard library only)

---

## Running the Program

```bash
python pcklbllwl.py
```

This script includes an example demo in:

`if __name__ == "__main__":`


You’ll see output for:
- adding players
- peeking next
- printing waitlist
- assigning a court

## Class API

`add_paddle(player_name)`  
Adds a player to the waitlist.

`peek_next()`  
Prints/returns the next player without removing them.

`assign_court()`  
Removes the next player and assigns them to a court.

`print_waitlist()`  
Prints the waitlist in correct order.
