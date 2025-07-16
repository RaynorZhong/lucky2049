from sqlmodel import Field, Session, SQLModel, create_engine, select
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from typing import List
import pandas as pd
import logging
import ast
import os

# env
CSV_NAME = "blockchain_timeup898560.csv"  # Name of the CSV file containing Bitcoin blockchain data
SQLITE_NAME = "database.db"  # Name of the SQLite database file

# models
class Bitcoin(SQLModel, table=True):
    height: int | None = Field(default=None, primary_key=True)
    hash: str = Field(index=True)
    timestamp: str

class Draw(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    front: str
    back: str
    timestamp: str
    start_height: int
    end_height: int

    @property
    def front_list(self) -> List[int]:
        return ast.literal_eval(self.front)
    
    @property
    def back_int(self) -> int:
        return int(self.back)

class Statistics(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    draws: int
    front_chi2: float
    front_p_value: float
    front_conclusion: str
    back_chi2: float
    back_p_value: float
    back_conclusion: str
    timestamp: str

class LogEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: str
    level: str
    logger_name: str
    message: str

class DatabaseHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self.level = level  # Explicitly set level attribute
    def emit(self, record):
        """
        Emit a log record to the database.
        
        :param record: Log record to be emitted.
        """
        log_entry = LogEntry(
            timestamp=datetime.now(),
            level=record.levelname,
            logger_name=record.name,
            message=record.getMessage()
        )
        with Session(engine) as session:
            session.add(log_entry)
            session.commit()

base_dir = os.path.dirname(os.path.abspath(__file__))
sqlite_file_name = os.path.join(base_dir, SQLITE_NAME)
csv_file_name = os.path.join(base_dir, CSV_NAME)
sqlite_url = f"sqlite:///{sqlite_file_name}"

# engine = create_engine(sqlite_url, echo=True)
engine = create_engine(sqlite_url)

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.addHandler(DatabaseHandler(level=logging.INFO))


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def create_bitcoin():
    if get_max_bitcoin_height() is not None:
        return
    csv_blockchain = pd.read_csv(csv_file_name, dtype=str)
    records = [
        Bitcoin(height=block[0], hash=block[1], timestamp=block[2])
        for block in csv_blockchain.itertuples(index=False, name=None)
    ]
    with Session(engine) as session:
        try:
            session.add_all(records)
            session.commit()
        except IntegrityError:
            session.rollback()
            # If any record fails (e.g., duplicate primary key), none are inserted  

def select_bitcoin_by_height(heights):
    start_height = min(heights)
    end_height = max(heights)
    with Session(engine) as session:
        statement = select(Bitcoin).where(Bitcoin.height.between(start_height, end_height)).order_by(Bitcoin.height)
        results = session.exec(statement)
        bitcoins = results.all()
        return bitcoins

def get_max_bitcoin_height():
    with Session(engine) as session:
        statement = select(Bitcoin.height).order_by(Bitcoin.height.desc()).limit(1)
        bitcoin_height = session.exec(statement).one_or_none()
        return bitcoin_height

def add_bitcoin(bitcoins):
    """
    Update or insert Bitcoin records in the database.
    
    :param bitcoins: List of tuples containing (height, hash, timestamp).
    """
    heights = [bitcoin[0] for bitcoin in bitcoins]
    if get_max_bitcoin_height() + 1 != min(heights):
        logger.warning("Bitcoin heights are not continuous, skipping update.")
        logger.warning(f"Expected height: {get_max_bitcoin_height() + 1}, but got: {min(heights)}")
        return False
    records = [
        Bitcoin(height=bitcoin[0], hash=bitcoin[1], timestamp=bitcoin[2])
        for bitcoin in bitcoins
    ]
    with Session(engine) as session:
        try:
            session.add_all(records)
            session.commit()
        except IntegrityError:
            session.rollback()
            # If any record fails (e.g., duplicate primary key), none are inserted

    return True

def create_draw(draws):
    records = [
        Draw(id=draw[0], front=str(draw[1]), back=str(draw[2]), timestamp=draw[3], start_height=draw[4], end_height=draw[5])
        for draw in draws
    ]
    with Session(engine) as session:
        try:
            session.add_all(records)
            session.commit()
        except IntegrityError:
            session.rollback()
            # If any record fails (e.g., duplicate primary key), none are inserted

def get_all_draws():
    with Session(engine) as session:
        statement = select(Draw).order_by(Draw.id)
        results = session.exec(statement)
        draws = results.all()
        return draws
    
def get_limit_draws(limit=20):
    with Session(engine) as session:
        statement = select(Draw).order_by(Draw.id.desc()).limit(limit)
        results = session.exec(statement)
        draws = results.all()
        return draws
    
def get_draw_by_id(draw_id):
    with Session(engine) as session:
        return session.get(Draw, draw_id)

def get_max_draw_id():
    with Session(engine) as session:
        statement = select(Draw.id).order_by(Draw.id.desc()).limit(1)
        daw_id = session.exec(statement).one_or_none()
        return daw_id

def create_statistics(statistics):
    records = [
        Statistics(
            draws=stat[0],
            front_chi2=stat[1],
            front_p_value=stat[2],
            front_conclusion=stat[3],
            back_chi2=stat[4],
            back_p_value=stat[5],
            back_conclusion=stat[6],
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        for stat in statistics
    ]
    with Session(engine) as session:
        try:
            session.add_all(records)
            session.commit()
        except IntegrityError:
            session.rollback()
            # If any record fails (e.g., duplicate primary key), none are inserted

def get_last_statistics():
    with Session(engine) as session:
        statement = select(Statistics).order_by(Statistics.id.desc()).limit(1)
        statistics = session.exec(statement).one_or_none()
        return statistics
    
def get_log_entries(page=1, page_size=50):
    """
    Retrieve log entries from the database.
    
    :param page: Page number for pagination (default is 1).
    :param page_size: Number of entries per page (default is 50).
    :return: List of log entries.
    """
    offset = (page - 1) * page_size
    with Session(engine) as session:
        statement = select(LogEntry).order_by(LogEntry.id.desc()).offset(offset).limit(page_size)
        results = session.exec(statement)
        logs = results.all()
        total_pages = (len(session.exec(select(LogEntry)).all()) + page_size - 1) // page_size
        return logs, total_pages

def init_db():
    create_db_and_tables()
    logger.info("Initializing database...")
    create_bitcoin()