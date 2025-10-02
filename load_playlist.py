import datetime
import pandas as pd 

def main():
    with open("/var/log/cron_output.log", "a") as f:
        f.write(f"Python script ran at: {datetime.datetime.now()}\n")
    print(f"Python script executed at {datetime.datetime.now()}")

    df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
    df.to_csv("/var/data/sample_output.csv", index=False)

if __name__ == "__main__":
    main()