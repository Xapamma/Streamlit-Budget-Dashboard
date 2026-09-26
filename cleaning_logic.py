import re
import pandas as pd
import numpy as np

# --- 1. DESCRIPTION CLEANING MAP ---
# Patterns are lowercase here for easier matching
description_map = {
    # --- RETAIL & SHOPPING ---
    r"wal\W*mart|wm superc|walmart": "Walmart Supercenter",
    r"costco whse|costco": "Costco Wholesale",
    r"amazon|amzn|amazon\.com|mktplace": "Amazon",
    r"ross stores|ross dress": "Ross",
    r"bath & body works": "Bath & Body Works",
    r"home depot": "Home Depot",
    r"maceys": "Macey's",
    r"shoeman\s*enterprise": "Shoeman Enterprise",
    r"we geek together": "We Geek Together",
    r"mcpaper": "McPaper",

    # --- DINING & TREATS ---
    r"mcdonald'?s": "McDonald's",
    r"chick[\-\s]?fil[\-\s]?a": "Chick-fil-A",
    r"taco bell": "Taco Bell",
    r"subway": "Subway",
    r"cafe zupas": "Cafe Zupas",
    r"panda express": "Panda Express",
    r"domino'?s": "Domino's Pizza",
    r"kneaders": "Kneaders",
    r"culver'?s": "Culver's",
    r"red cliffs boba": "Red Cliffs Boba Tea",
    r"cold stone": "Cold Stone Creamery",
    r"tst\*?brookers|brookers": "Brooker's Ice Cream",
    r"popbite": "Popbite Bakery",
    r"burger king": "Burger King",
    r"carls?\s?jr": "Carl's Jr.",
    r"chipotle": "Chipotle",
    r"del taco": "Del Taco",
    r"noodles?\s*&?\s*company": "Noodles And Company",
    r"papa john'?s": "Papa John's",
    r"papa murphy'?s": "Papa Murphy's",
    r"johnny rockets": "Johnny Rockets",
    r"yogurtland": "Yogurtland",
    r"lindt": "Lindt",
    r"milk*cookies": "Milk & Cookies",
    r"cheesecake": "Cheesecake Factory",

    # --- INTERNATIONAL FOOD (important for your data) ---
    r"rewe": "Rewe Markt",
    r"backwerk": "Backwerk",
    r"kamps": "Kamps Berlin",
    r"le crobag": "Le Crobag De",
    r"landbaeckerei": "Landbaeckerei Schmidt",
    r"confiserie felicitas": "Confiserie Felicitas",

    # --- TRANSPORTATION & AUTO ---
    r"costco gas|costco fuel": "Costco Gas",
    r"maverik|maverick": "Maverik",
    r"shell": "Shell",
    r"chevron": "Chevron",
    r"jiffy lube": "Jiffy Lube",
    r"\bdmv\b": "DMV",
    r"autozone": "Autozone",

    # --- HEALTH & SERVICES ---
    r"oak hills pharmacy": "Oak Hills Pharmacy",
    r"walgreens": "Walgreens",
    r"crosspointe dental": "Crosspointe Dental",
    r"bear river": "Bear River Insurance",
    r"t[-\s]?mobile": "T-Mobile",
    r"google": "Google Services",
    r"vpn": "Nord VPN",
    r"west jordan ut card": "West Jordan Dental",
    r"hearts nail spa": "Hearts Nail Spa",

    # --- EDUCATION ---
    r"ebook academy": "Ebook Academy",
    r"byu refund|brigham young un": "BYU Refund",
    r"byu|cougar cash|byu store": "BYU (Campus Services)",

    # --- DONATIONS---
    r"ch jesuschrist|church of jesus|lds philanthr|donation": "Church Donations",
    r"life\s*starfish": "Life Starfish",
    r"grandma becky": "Grandma Becky Payment",

    # --- ENTERTAINMENT ---
    r"dancefitme": "Dancefitme Fun",
    r"fandango|vudu": "Fandango/Vudu",
    r"steamgamescom": "Steam (Gaming)",
    r"cinemark": "Cinemark",
    r"lava hot": "Lava Hot Springs",
    r"lagoon": "Lagoon",
    r"cornbellys": "Shopcornbellys",
    r"espn": "ESPN",

    # --- TRAVEL ---
    r"delta": "Delta",
    r"klm": "KLM",
    r"airport|flughafen": "Flughafen Berlin Brand",
    r"arcotel": "Arcotel Hafencity",
    r"hotel johann": "Hotel Johann",
    r"elaya": "Elaya Hotel",

    # --- INCOME / FINANCE ---
    r"deposit dividend|interest earned|interest payment": "Interest Earned",
    r"dividend": "Dividend Income",
    r"payroll|salary": "Payroll Deposit",
    r"visa.*fee": "Visa Service Fee",
    r"robinhood": "Robinhood Investment",
    r"deposit.*reim.*mileage": "Mileage Reimbursement",
    r"rewards from golden": "Youth Engagement Promo Reward",
    r"deposit promo deposit youth engagement": "Youth Engagement Promo Reward",

    # --- MISC / TRANSFERS ---
    r"home banking transfer": "Internal Transfer",
    r"pos withdrawal": "Debit Purchase",
    r"payment to perks": "Perks Payment",
    r"capital one member fee": "Capital One Membership Fee",
    r"bank withdrawal": "Bank Withdrawal",
}

description_merchants = list(set(description_map.values()))

# --- 2. SUB-CATEGORY MAP ---
sub_category_map = {
    # --- Food / Dining ---
    "Walmart Supercenter": "Groceries",
    "Costco Wholesale": "Groceries",
    "Macey's": "Groceries",
    "Jacksons Food Stores": "Groceries",
    "Aldi Nord": "Groceries",
    "Volker's Bakery": "Groceries",
    "Life Starfish": "Groceries",
    "McDonald's": "Fast Food",
    "Chick-fil-A": "Fast Food",
    "Taco Bell": "Fast Food",
    "Subway": "Fast Food",
    "Panda Express": "Fast Food",
    "Domino's Pizza": "Fast Food",
    "Kneaders": "Fast Food",
    "Culver's": "Fast Food",
    "Red Cliffs Boba Tea": "Fast Food",
    "Cold Stone Creamery": "Snacks",
    "Brooker's Ice Cream": "Snacks",
    "Popbite Bakery": "Fast Food",
    "Backwerk": "Fast Food",
    "Burger King": "Fast Food",
    "Carl's Jr.": "Fast Food",
    "Chipotle": "Fast Food",
    "Del Taco": "Fast Food",
    "Noodles And Company": "Fast Food",
    "Papa John's": "Fast Food",
    "Papa Murphy's": "Fast Food",
    "Johnny Rockets": "Fast Food",
    "Wiener Feinbackerei": "Fast Food",
    "Yogurtland": "Snacks",
    "Cafe Zupas": "Restaurants/Dining",
    "Cheesecake": "Restaurants/Dining",
    "Golden Corral": "Restaurants/Dining",
    "Ristorante Calice Doro": "Restaurants/Dining",
    "Texas Roadhouse": "Restaurants/Dining",
    "Rewe Markt": "Groceries",
    "Landbaeckerei Schmidt": "Fast Food",
    "Le Crobag De": "Fast Food",
    "Lindt": "Snacks",
    "Confiserie Felicitas": "Fast Food",
    "Kamps Berlin": "Fast Food",
    "Milk & Cookies": "Snacks",
    "Nielsen's Frozen Custard": "Fast Food",
    "Roxberry Juice Co.": "Fast Food",
    "Chromis Bakery": "Fast Food",
    "Willy Dany": "Fast Food",
    "Dolce Freddo": "Fast Food",
    "Leatherby'S Family Cream": "Fast Food",
    "Fabulous Freddy's": "Fast Food",
    "Cheesecake Factory": "Restaurants/Dining",

    # --- Automotive & Fuel ---
    "Costco Gas": "Fuel",
    "Maverik": "Fuel",
    "Shell": "Fuel",
    "Chevron": "Fuel",
    "Bear River Insurance": "Insurance",
    "Jiffy Lube": "Maintenance",
    "DMV": "Licensing",
    "SLC Airport Parking": "Maintenance",
    "Autozone": "Repairs",
    "Zephyr Lot": "Parking",

    # --- Health & Wellness ---
    "Oak Hills Pharmacy": "Pharmacy",
    "Walgreens": "Pharmacy",
    "Crosspointe Dental": "Medical",
    "Bath & Body Works": "Personal Care",
    "Hearts Nail Spa": "Personal Care",
    "Rossmann": "Personal Care",
    "Victoria's Secret": "Personal Care",
    "City-Apotheken": "Pharmacy",
    "Winters Custom Hair": "Personal Care",
    "Tie One On": "Personal Care",
    "West Jordan Dental": "Doctor/Dentist",
    
    # --- Travel & Lodging ---
    "Delta": "Airfare",
    "KLM": "Airfare",
    "Cotflt": "Airfare",
    "Flughafen Berlin Brand": "Travel & Commute",
    "Grandma Becky Payment": "Lodging",
    "Arcotel Hafencity": "Lodging",
    "Elaya Hotel": "Lodging",
    "Hotel Johann": "Lodging",
    "Cothtl": "Lodging",
    "Areas Roissy": "Travel & Commute",
    "Hms Host International": "Travel & Commute",
    "Mopla Bewegt": "Travel & Commute",

    # --- Shopping & Supplies ---
    "Amazon": "General Retail",
    "Ross": "General Retail",
    "McPaper": "General Retailer",
    "Barnes & Noble": "General Retail",
    "Dick's Sporting Goods": "General Retail",
    "Downeast": "Clothing",
    "Jcpenney": "Clothing",
    "Maurices": "Clothing",
    "Spirit Halloween": "Clothing",
    "Home Depot": "Home Improvement",
    "Hobby Lobby": "Hobbies",
    "Sweetwater-Recordstore": "Hobbies",
    "Enticon Shops": "General Retail",
    "Post Mart": "General Retail",
    "Saturn Electro-Handels": "General Retail",
    "Tricked Out Accessories": "General Retail",
    "Famous Footwear": "Clothing",
    "Shoeman Enterprise": "Clothing",
    "Bm Weihnachtsland": "General Retail",
    "Fye University Place": "General Retail",
    "DI": "General Retail",
    "We Geek Together": "General Retail",

    # --- Housing & Bills ---
    "T-Mobile": "Phone",
    "Cbtutah County Clerk": "Service Fees",
    "Cbtsvc Fee Utah Cnty": "Service Fees",
    "Google Services": "Service Fees",
    "Visa Service Fee": "Service Fees",
    "Capital One Member Fee": "Service Fees",
    "Hrb Online Tax Product": "Service Fees",
    "Nord VPN": "Service Fees",
    "Ut Hunt/Fish Lic": "Service Fees",
    
    # --- Gifts & Donations ---
    "Church Donations": "Tithing",

    # --- Education ---
    "BYU (Campus Services)": "Education",
    "Ebook Academy": "Books",

    # --- Entertainment ---
    "Steam (Gaming)": "Gaming",
    "Fandango/Vudu": "Streaming/Movies",
    "Cinemark": "Streaming/Movies",
    "Lava Hot Springs": "Recreation",
    "Dancefitme Fun": "Recreation",
    "Lagoon": "Recreation",
    "Shopcornbellys": "Recreation",
    "ESPN": "Subscriptions",
    "Provo City Cntr Temple": "Recreation",
    "Voelkerschlachtdenkmal": "Recreation",

    # --- Savings & Investments ---
    "Interest Earned": "Dividends",
    "Dividend Income": "Dividends",
    "Investment Income": "Dividends",
    "Robinhood Investment": "Investments",

    # --- Income ---
    "Payroll Deposit": "Paychecks",
    "BYU Refund": "Refunds",
    "Credit Travel Reward": "CC Rewards",
    "Youth Engagement Promo": "Other Income",
    "Mileage Reimbursement": "Other Income",
    "Youth Engagement Promo Reward": "Other Income",
}

# Makes a list of known merchants to prioritize
merchants_list = list(set(sub_category_map.keys()))

# --- 3. CATEGORY HIERARCHY ---
category_hierarchy = {
    "Food & Dining": ["Groceries", "Restaurants/Dining", "Fast Food", "Snacks"],
    "Transportation": ["Vehicle Payments", "Auto Insurance", "Fuel", "Licensing", "Parking", "Repairs", "Maintenance"],
    "Health & Wellness": ["Doctor/Dentist", "Medicine/Drugs", "Personal Care", "Pharmacy", "Health Insurance"],
    "Travel & Lodging": ["Airfare", "Lodging", "Travel & Commute"],
    "Shopping & Supplies": ["General Retail", "Home Improvement", "Hobbies", "Clothing"], 
    "Housing & Bills": ["Mortgage/Rent", "Phone", "Cable/Internet", "Service Fees", "Utilities", "Maintenance/Repairs"],
    "Gifts & Donations": ["Tithing", "Fast Offerings", "Hum Aid", "Gift"],
    "Education": ["Education", "Tutition", "Books"],
    "Entertainment": ["Gaming", "Subscriptions", "Recreation"],
    "Savings & Investments": ["Dividends", "Investments", "Emergency Fund", "Retirement", "House Down Payment"],
    "Income": ["Paychecks", "Refunds", "CC Rewards", "Other Income"],
    "Miscellaneous": ["Fees & Charges", "Other Services"], 
    "Vacations / Travel": ["Airfare", "Travel", "Lodging", "Food", "Entertainment", "Souvenirs"]    
}

# Connects sub to main title
sub_to_main = {sub: main for main, subs in category_hierarchy.items() for sub in subs}

# Makes a smart title
def smart_title(text):

    words = text.lower().split()
    fixed = []

    for w in words:
        if "'" in w:
            first, rest = w.split("'", 1)
            fixed.append(first.capitalize() + "'" + rest.lower())
        else:
            fixed.append(w.capitalize())

    return " ".join(fixed)

# Basic cleaning for descriptions
def clean_description_for_matching(description):
    raw_desc = str(description).lower()

    # Remove bank noise
    raw_desc = re.sub(r'^\s*withdrawal\s*$', 'bank withdrawal', raw_desc)

    raw_desc = re.sub(
        r'^withdrawal\s*(xx)?\s*x?\b|xx\s*(sq|card|[a-z])?|^recurring\s*withdrawal\s*|provo\s*ut|\borem\b',
        '',
        raw_desc
    )


    # 🔥 ADD THIS
    raw_desc = re.sub(r"[^a-z&'\-\s]", '', raw_desc)  # removes numbers + symbols

    # Normalize spacing
    raw_desc = " ".join(raw_desc.split())
    
    return raw_desc

# Check the regex cleaning first
def match_description_map(raw_desc):
    for pattern, merchant in description_map.items():
        if re.search(pattern, raw_desc):
            return merchant
    return None


def add_categories(df):

    def process_row(row):
        merchant = row['merchant']
        amt = row['amount']
        description = row['description']

        # Determine Sub-Category
        sub = sub_category_map.get(merchant, "Other")
        
        # Determine Main Category with "General Spending" and "Other Income" fallbacks
        if sub in sub_to_main:
            main = sub_to_main[sub]
        elif amt < 0:
            main = "General Spending"
        else:
            main = "Other Income"
        
        if description is None:
            return pd.Series([None, None, None])
            
        return pd.Series([main, sub])

    # Apply across rows
    df[['main_category', 'sub_category']] = df.apply(process_row, axis=1)
    
    # Ensure description is Title Case for final output
    df['description'] = df['description'].apply(smart_title)

    return df
