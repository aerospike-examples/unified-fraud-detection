#!/usr/bin/env python3
"""
Generate user data for fraud detection application with Aerospike Graph CSV format.
Creates users, accounts, devices with realistic fraud patterns for banking scenarios.
"""

import json
import random
import argparse
import os
from datetime import datetime, timedelta
from faker import Faker
from pathlib import Path
import csv

# Setup faker instances for different regions
fake_us = Faker('en_US')
fake_in = Faker('en_IN')
fake_gb = Faker('en_GB')
fake_au = Faker('en_AU')
fake_cn = Faker('zh_CN')

def set_seeds(seed=42):
    """Set random seeds for reproducible data generation"""
    Faker.seed(seed)
    random.seed(seed)

# Regional data configurations
REGIONAL_DATA = {
    'american': {
        'faker': fake_us,
        'cities': [
            "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", 
            "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville",
            "Fort Worth", "Columbus", "Charlotte", "San Francisco", "Indianapolis", 
            "Seattle", "Denver", "Washington", "Boston", "Nashville", "Detroit",
            "Portland", "Las Vegas", "Memphis", "Louisville", "Baltimore", "Milwaukee",
            "Atlanta", "Kansas City", "Miami", "Colorado Springs", "Raleigh"
        ],
        'banks': [
            "Chase Bank", "Wells Fargo", "Bank of America", "Citibank", "U.S. Bank",
            "PNC Bank", "Capital One", "TD Bank", "BB&T", "SunTrust Bank",
            "Regions Bank", "Fifth Third Bank", "KeyBank", "Huntington Bank"
        ],
        'phone_format': '+1-{area}-{exchange}-{number}',
        'occupations': [
            "Software Engineer", "Marketing Manager", "Financial Analyst", "Sales Representative",
            "Teacher", "Accountant", "Nurse", "Police Officer", "Graphic Designer", 
            "Project Manager", "Data Scientist", "Construction Manager", "HR Specialist",
            "Web Developer", "Real Estate Agent", "Doctor", "Lawyer", "Chef", "Electrician",
            "Plumber", "Mechanic", "Dentist", "Architect", "Engineer", "Consultant"
        ]
    },
    'indian': {
        'faker': fake_in,
        'cities': [
            "Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Ahmedabad", "Chennai", "Kolkata",
            "Pune", "Jaipur", "Lucknow", "Kanpur", "Nagpur", "Indore", "Thane", "Bhopal",
            "Visakhapatnam", "Pimpri-Chinchwad", "Patna", "Vadodara", "Ghaziabad", "Ludhiana",
            "Agra", "Nashik", "Faridabad", "Meerut", "Rajkot", "Kalyan-Dombivli", "Vasai-Virar",
            "Varanasi", "Srinagar", "Dhanbad", "Jodhpur", "Amritsar", "Raipur", "Allahabad"
        ],
        'banks': [
            "State Bank of India", "HDFC Bank", "ICICI Bank", "Punjab National Bank",
            "Canara Bank", "Union Bank of India", "Axis Bank", "Bank of Baroda",
            "Indian Overseas Bank", "Central Bank of India", "Indian Bank", "Yes Bank",
            "Kotak Mahindra Bank", "Federal Bank", "IDBI Bank", "Syndicate Bank"
        ],
        'phone_format': '+91-{area}-{number}',
        'occupations': [
            "Software Engineer", "Teacher", "Accountant", "Sales Representative",
            "Marketing Manager", "Nurse", "Police Officer", "Data Scientist",
            "HR Specialist", "Web Developer", "Graphic Designer", "Financial Analyst",
            "Project Manager", "Real Estate Agent", "Construction Manager", "Doctor",
            "Government Officer", "Bank Manager", "Shopkeeper", "Farmer", "Driver",
            "Engineer", "Consultant", "Business Owner", "Professor"
        ]
    },
    'en_GB': {
        'faker': fake_gb,
        'cities': [
            "London", "Manchester", "Birmingham", "Edinburgh", "Glasgow", "Liverpool",
            "Leeds", "Bristol", "Sheffield", "Newcastle", "Cardiff", "Belfast",
            "Nottingham", "Southampton", "Brighton", "Leicester", "Coventry",
            "Hull", "Bradford", "Stoke-on-Trent", "Wolverhampton", "Derby",
            "Reading", "Plymouth", "Northampton", "Luton", "Aberdeen", "Portsmouth",
            "Milton Keynes", "Swindon", "Dundee", "York", "Oxford", "Cambridge"
        ],
        'banks': [
            "Barclays", "HSBC", "Lloyds Bank", "NatWest", "Santander UK",
            "Standard Chartered", "Nationwide", "Royal Bank of Scotland",
            "TSB Bank", "Virgin Money", "Metro Bank", "Starling Bank",
            "Monzo", "Revolut", "First Direct"
        ],
        'phone_format': '+44-{area}-{number}',
        'occupations': [
            "Software Engineer", "Marketing Manager", "Financial Analyst", "Sales Representative",
            "Teacher", "Accountant", "Nurse", "Police Officer", "Graphic Designer",
            "Project Manager", "Data Scientist", "Construction Manager", "HR Specialist",
            "Web Developer", "Real Estate Agent", "Doctor", "Solicitor", "Chef", "Electrician",
            "Plumber", "Mechanic", "Dentist", "Architect", "Engineer", "Consultant"
        ]
    },
    'en_AU': {
        'faker': fake_au,
        'cities': [
            "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast",
            "Newcastle", "Canberra", "Sunshine Coast", "Wollongong", "Hobart",
            "Geelong", "Townsville", "Cairns", "Darwin", "Toowoomba", "Ballarat",
            "Bendigo", "Launceston", "Mackay", "Rockhampton", "Bundaberg",
            "Coffs Harbour", "Wagga Wagga", "Hervey Bay", "Port Macquarie",
            "Orange", "Dubbo", "Nowra", "Bathurst", "Warrnambool", "Kalgoorlie",
            "Bunbury", "Rockingham", "Mandurah", "Albany", "Geraldton"
        ],
        'banks': [
            "Commonwealth Bank", "Westpac", "NAB", "ANZ", "Macquarie Bank",
            "Bendigo Bank", "Bank of Queensland", "Suncorp Bank", "ING Australia",
            "Bankwest", "Beyond Bank", "Heritage Bank", "People's Choice",
            "Great Southern Bank", "Bank Australia"
        ],
        'phone_format': '+61-{area}-{number}',
        'occupations': [
            "Software Engineer", "Marketing Manager", "Financial Analyst", "Sales Representative",
            "Teacher", "Accountant", "Nurse", "Police Officer", "Graphic Designer",
            "Project Manager", "Data Scientist", "Construction Manager", "HR Specialist",
            "Web Developer", "Real Estate Agent", "Doctor", "Lawyer", "Chef", "Electrician",
            "Plumber", "Mechanic", "Dentist", "Architect", "Engineer", "Consultant"
        ]
    },
    'zh_CN': {
        'faker': fake_cn,
        'cities': [
            "Beijing", "Shanghai", "Guangzhou", "Shenzhen", "Hangzhou", "Chengdu",
            "Wuhan", "Xi'an", "Tianjin", "Nanjing", "Suzhou", "Zhengzhou",
            "Changsha", "Shenyang", "Qingdao", "Dalian", "Ningbo", "Xiamen",
            "Kunming", "Hefei", "Foshan", "Fuzhou", "Wuxi", "Nantong",
            "Dongguan", "Zhongshan", "Zhuhai", "Jinan", "Harbin", "Changchun",
            "Taiyuan", "Nanning", "Guiyang", "Lanzhou", "Haikou", "Yinchuan",
            "Hohhot", "Urumqi", "Lhasa"
        ],
        'banks': [
            "ICBC", "China Construction Bank", "Agricultural Bank of China", "Bank of China",
            "Bank of Communications", "China Merchants Bank", "Shanghai Pudong Development Bank",
            "Industrial Bank", "China Minsheng Bank", "China CITIC Bank",
            "China Everbright Bank", "Ping An Bank", "Huaxia Bank", "Guangdong Development Bank",
            "Beijing Bank", "Bank of Nanjing"
        ],
        'phone_format': '+86-{area}-{number}',
        'occupations': [
            "Software Engineer", "Marketing Manager", "Financial Analyst", "Sales Representative",
            "Teacher", "Accountant", "Nurse", "Police Officer", "Graphic Designer",
            "Project Manager", "Data Scientist", "Construction Manager", "HR Specialist",
            "Web Developer", "Real Estate Agent", "Doctor", "Lawyer", "Chef", "Electrician",
            "Plumber", "Mechanic", "Dentist", "Architect", "Engineer", "Consultant"
        ]
    }
}

# Device configurations
DEVICE_TYPES = ["mobile", "desktop", "tablet"]
OPERATING_SYSTEMS = {
    "mobile": ["Android 13", "Android 12", "iOS 16", "iOS 15", "Android 11"],
    "desktop": ["Windows 11", "Windows 10", "macOS Ventura", "macOS Monterey", "Ubuntu 22.04"],
    "tablet": ["iPadOS 16", "Android 12", "iPadOS 15", "Android 11"]
}
BROWSERS = {
    "mobile": ["Chrome Mobile", "Safari Mobile", "Firefox Mobile", "Samsung Internet"],
    "desktop": ["Chrome", "Firefox", "Safari", "Edge", "Opera"],
    "tablet": ["Safari", "Chrome", "Firefox"]
}

ACCOUNT_TYPES = ["savings", "checking", "credit"]

class UserDataGenerator:
    def __init__(self, num_users, region, output_dir):
        self.num_users = num_users
        self.region = region
        self.output_dir = Path(output_dir)
        self.config = REGIONAL_DATA[region]
        self.faker = self.config['faker']
        
        # Data storage
        self.users = []
        self.accounts = []
        self.devices = []
        self.owns_edges = []
        self.uses_edges = []
        
        # Device sharing patterns for fraud detection - OPTIMIZED
        self.device_pool = []
        self.shared_device_groups = []
        
        # Scalable device management
        self.device_counter = 0  # Counter for efficient device allocation
        self.max_devices = 0     # Total device pool size
        self.device_cache = {}   # Cache for device objects
        self.allocated_devices = set()  # Track allocated device IDs
        
    def generate_device_pool(self):
        """Create a scalable device pool without storing all devices in memory"""
        # Calculate device pool size with reasonable caps for scalability
        base_devices = int(self.num_users * 3.5)  # 3.5x multiplier for safety
        min_devices = self.num_users + 500  # Ensure at least 1 device per user + buffer
        
        # Cap device pool for very large datasets to prevent memory issues
        max_reasonable_devices = min(10_000_000, base_devices)  # Cap at 10M devices
        self.max_devices = max(min_devices, max_reasonable_devices)
        
        print(f"Creating scalable device pool: {self.max_devices:,} devices for {self.num_users:,} users (ratio: {self.max_devices/self.num_users:.1f}x)")
        
        # Pre-generate shared devices for fraud patterns (small subset)
        self._generate_shared_devices_only()
        
        print(f"Pre-generated {len(self.device_pool)} shared devices, remaining {self.max_devices - len(self.device_pool):,} will be created on-demand")
    
    def _generate_shared_devices_only(self):
        """Generate only the devices needed for shared device groups"""
        # Estimate devices needed for sharing patterns
        estimated_shared_devices = min(1000, self.num_users // 10)  # Conservative estimate
        
        for i in range(estimated_shared_devices):
            device = self._create_device(i + 1)
            self.device_pool.append(device)
    
    def _create_device(self, device_num):
        """Create a single device object"""
        device_type = random.choice(DEVICE_TYPES)
        return {
            'id': f"DEV{str(device_num).zfill(7)}",  # 7 digits for 10M+ devices
            'type': device_type,
            'os': random.choice(OPERATING_SYSTEMS[device_type]),
            'browser': random.choice(BROWSERS[device_type]),
            'fingerprint': self.faker.sha256(),
            'first_seen': (datetime.now() - timedelta(days=random.randint(0, 500))).strftime('%Y-%m-%dT%H:%M:%SZ')
        }
    
    def _allocate_devices_efficiently(self, count, user_id):
        """Efficiently allocate devices using counter-based approach"""
        if self.device_counter + count > self.max_devices:
            # Not enough devices remaining
            available_count = self.max_devices - self.device_counter
            count = max(0, available_count)
        
        if count == 0:
            print(f"❌ No devices available for user {user_id}")
            return []
        
        # Allocate devices using counter (O(1) operation)
        allocated_devices = []
        for i in range(count):
            device_num = self.device_counter + 1 + i
            device = self._create_device(device_num)
            allocated_devices.append(device)
            self.allocated_devices.add(device['id'])
        
        self.device_counter += count
        return allocated_devices
    
    def create_shared_device_groups(self):
        """Create device sharing patterns for fraud detection - OPTIMIZED"""
        # Scale groups more conservatively for large datasets
        num_groups = min(100, max(3, self.num_users // 50))  # Cap at 100 groups
        
        print(f"Creating {num_groups} shared device groups for fraud patterns...")
        
        for i in range(num_groups):
            if i == 0:
                # Highly suspicious: 3-4 users sharing 2 devices
                group_size = random.randint(3, 4)
                num_shared_devices = 2
                group_type = "highly_suspicious"
            elif i == 1:
                # Moderately suspicious: 2 users sharing 3 devices
                group_size = 2
                num_shared_devices = 3
                group_type = "moderately_suspicious"
            else:
                # Family/household: 2-3 users sharing 1-2 devices
                group_size = random.randint(2, 3)
                num_shared_devices = random.randint(1, 2)
                group_type = "family_sharing"
            
            # Select random users for this group (scattered throughout user range)
            if self.num_users >= group_size:
                user_indices = random.sample(range(self.num_users), group_size)
                
                # Select devices from pre-generated pool
                if len(self.device_pool) >= num_shared_devices:
                    shared_devices = random.sample(self.device_pool, num_shared_devices)
                    
                    group = {
                        'users': user_indices,
                        'devices': shared_devices,
                        'type': group_type
                    }
                    self.shared_device_groups.append(group)
                    
                    # Remove shared devices from pool to prevent re-use
                    for device in shared_devices:
                        self.device_pool.remove(device)
                        self.allocated_devices.add(device['id'])
    
    def _generate_phone(self):
        """Generate region-specific phone number."""
        if self.region == 'american':
            return f"+1-{random.randint(200, 999)}-{random.randint(200, 999)}-{random.randint(1000, 9999)}"
        if self.region == 'indian':
            return f"+91-{random.randint(70000, 99999)}-{random.randint(10000, 99999)}"
        if self.region == 'en_GB':
            return f"+44-{random.randint(10, 99)}-{random.randint(1000, 9999)}-{random.randint(100000, 999999)}"
        if self.region == 'en_AU':
            return f"+61-{random.randint(2, 9)}{random.randint(10000000, 99999999)}"
        if self.region == 'zh_CN':
            return f"+86-{random.randint(10, 99)}-{random.randint(10000000, 99999999)}"
        return f"+1-{random.randint(200, 999)}-{random.randint(200, 999)}-{random.randint(1000, 9999)}"

    def generate_user(self, user_index):
        """Generate a single user with realistic data"""
        user_id = f"U{str(user_index + 1).zfill(7)}"  # 7 digits to support millions of users
        
        name = self.faker.name()
        phone = self._generate_phone()
        
        email = f"{name.lower().replace(' ', '.')}@{self.faker.domain_name()}"
        age = random.randint(18, 70)
        location = random.choice(self.config['cities'])
        occupation = random.choice(self.config['occupations'])
        risk_score = 0.0
        
        # Generate signup date within last 2 years
        signup_date = (datetime.now() - timedelta(days=random.randint(0, 730))).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        user = {
            'id': user_id,
            'name': name,
            'email': email,
            'phone': phone,
            'age': age,
            'location': location,
            'occupation': occupation,
            'risk_score': risk_score,
            'signup_date': signup_date
        }
        
        return user
    
    def generate_accounts_for_user(self, user_id, user_index):
        """Generate 1-4 accounts for a user"""
        num_accounts = random.choices([1, 2, 3, 4], weights=[0.3, 0.4, 0.2, 0.1])[0]
        user_accounts = []
        
        for i in range(num_accounts):
            account_id = f"A{str(user_index + 1).zfill(7)}{str(i + 1).zfill(2)}"  # Support millions of users
            account_type = random.choice(ACCOUNT_TYPES)
            
            if account_type == "credit":
                balance = round(random.uniform(-50000, 0), 2)
            elif account_type == "savings":
                balance = round(random.uniform(1000, 500000), 2)
            else:  # checking
                balance = round(random.uniform(100, 50000), 2)
            
            bank_name = random.choice(self.config['banks'])
            created_date = (datetime.now() - timedelta(days=random.randint(0, 1000))).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Generated data: fraud flag always false
            fraud_flag = False
            
            account = {
                'id': account_id,
                'type': account_type,
                'balance': balance,
                'bank_name': bank_name,
                'status': 'active',
                'created_date': created_date,
                'fraud_flag': fraud_flag
            }
            
            user_accounts.append(account)
            
            # Create ownership edge
            self.owns_edges.append({
                'from': user_id,
                'to': account_id,
                'since': created_date
            })
        
        return user_accounts
    
    def generate_devices_for_user(self, user_id, user_index):
        """Generate 1-5 devices for a user with fraud patterns - OPTIMIZED"""
        user_devices = []
        
        # Check if user is in any shared device group
        user_in_shared_group = None
        for group in self.shared_device_groups:
            if user_index in group['users']:
                user_in_shared_group = group
                break
        
        if user_in_shared_group:
            # User shares devices with others
            user_devices.extend(user_in_shared_group['devices'])
            
            # 40% chance to also have 1-2 personal devices
            if random.random() < 0.4:
                personal_device_count = random.randint(1, 2)
                # Use efficient allocation for personal devices
                personal_devices = self._allocate_devices_efficiently(personal_device_count, user_id)
                user_devices.extend(personal_devices)
        else:
            # Regular user gets 1-5 unique devices based on realistic distribution
            desired_count = random.choices([1, 2, 3, 4, 5], weights=[0.15, 0.35, 0.30, 0.15, 0.05])[0]
            
            # Calculate remaining capacity for reservation (prevent starvation)
            remaining_users = max(0, self.num_users - user_index - 1)
            remaining_capacity = max(0, self.max_devices - self.device_counter)
            
            if remaining_capacity > 0:
                # Reserve capacity for remaining users (at least 1 device per user)
                reservable = max(0, remaining_capacity - remaining_users)
                max_for_user = min(desired_count, max(1, reservable + 1))
                
                # Allocate devices efficiently
                user_devices = self._allocate_devices_efficiently(max_for_user, user_id)
                
                if len(user_devices) < desired_count and len(user_devices) > 0:
                    if user_index % 1000 == 0:  # Only log every 1000th user to avoid spam
                        print(f"⚠️ User {user_id} wanted {desired_count} devices but got {len(user_devices)} (reserved capacity for remaining users)")
            else:
                if user_index % 1000 == 0:  # Only log every 1000th user
                    print(f"❌ Warning: User {user_id} got 0 devices! Device pool exhausted.")
                user_devices = []
        
        # Add user-specific properties to devices and create usage edges
        for device in user_devices:
            last_login = (datetime.now() - timedelta(days=random.randint(0, 30), 
                                                   hours=random.randint(0, 23))).strftime('%Y-%m-%dT%H:%M:%SZ')
            login_count = random.randint(5, 200)
            
            # Update device with user-specific data
            device['last_login'] = last_login
            device['login_count'] = login_count
            device['fraud_flag'] = False  # Generated data: always false
            
            # Create usage edge
            self.uses_edges.append({
                'from': user_id,
                'to': device['id'],
                'first_used': device['first_seen'],
                'last_used': last_login,
                'usage_count': login_count
            })
        
        return user_devices
    
    def generate_all_data(self):
        """Generate all users, accounts, and devices - OPTIMIZED for large datasets"""
        print(f"Generating {self.num_users:,} {self.region} users with fraud patterns...")
        
        # Create device pool and sharing patterns
        self.generate_device_pool()
        self.create_shared_device_groups()
        
        # Determine batch size based on dataset size
        if self.num_users <= 10000:
            progress_interval = 50
            batch_size = 1000
        elif self.num_users <= 100000:
            progress_interval = 1000
            batch_size = 5000
        else:
            progress_interval = 10000
            batch_size = 10000
        
        print(f"Processing in batches of {batch_size:,} users...")
        
        # Generate users and their relationships with batching
        for i in range(self.num_users):
            # Generate user
            user = self.generate_user(i)
            self.users.append(user)
            
            # Generate accounts for user
            user_accounts = self.generate_accounts_for_user(user['id'], i)
            self.accounts.extend(user_accounts)
            
            # Generate devices for user
            user_devices = self.generate_devices_for_user(user['id'], i)
            # Only add devices that aren't already in the list (use set for O(1) lookup)
            existing_device_ids = {d['id'] for d in self.devices}
            for device in user_devices:
                if device['id'] not in existing_device_ids:
                    self.devices.append(device)
                    existing_device_ids.add(device['id'])
            
            # Progress reporting
            if (i + 1) % progress_interval == 0:
                progress_pct = ((i + 1) / self.num_users) * 100
                devices_allocated = len(self.allocated_devices)
                print(f"Generated {i + 1:,} users ({progress_pct:.1f}%) - Devices allocated: {devices_allocated:,}")
            
            # Memory management for very large datasets
            if (i + 1) % batch_size == 0 and self.num_users > 50000:
                # Could implement batch writing here if memory becomes an issue
                pass
    
    def create_output_directories(self):
        """Create the required directory structure for Aerospike Graph CSV format"""
        # Create main directories
        vertices_dir = self.output_dir / "vertices"
        edges_dir = self.output_dir / "edges"
        
        # Create subdirectories for different vertex types
        (vertices_dir / "users").mkdir(parents=True, exist_ok=True)
        (vertices_dir / "accounts").mkdir(parents=True, exist_ok=True)
        (vertices_dir / "devices").mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories for different edge types
        (edges_dir / "ownership").mkdir(parents=True, exist_ok=True)
        (edges_dir / "usage").mkdir(parents=True, exist_ok=True)
    
    def write_csv_files(self):
        """Write data to CSV files in Aerospike Graph format"""
        self.create_output_directories()
        
        # Write users vertex file
        users_file = self.output_dir / "vertices" / "users" / "users.csv"
        with open(users_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Header with property types
            writer.writerow([
                '~id', '~label', 'name:String', 'email:String', 'phone:String',
                'age:Int', 'location:String', 'occupation:String', 
                'risk_score:Double', 'signup_date:Date'
            ])
            
            for user in self.users:
                writer.writerow([
                    user['id'], 'user', user['name'], user['email'], user['phone'],
                    user['age'], user['location'], user['occupation'],
                    user['risk_score'], user['signup_date']
                ])
        
        # Write accounts vertex file
        accounts_file = self.output_dir / "vertices" / "accounts" / "accounts.csv"
        with open(accounts_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                '~id', '~label', 'type:String', 'balance:Double', 'bank_name:String',
                'status:String', 'created_date:Date', 'fraud_flag:Boolean'
            ])
            
            for account in self.accounts:
                writer.writerow([
                    account['id'], 'account', account['type'], account['balance'],
                    account['bank_name'], account['status'], account['created_date'],
                    account['fraud_flag']
                ])
        
        # Write devices vertex file
        devices_file = self.output_dir / "vertices" / "devices" / "devices.csv"
        with open(devices_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                '~id', '~label', 'type:String', 'os:String', 'browser:String',
                'fingerprint:String', 'first_seen:Date', 'last_login:Date',
                'login_count:Int', 'fraud_flag:Boolean'
            ])
            
            for device in self.devices:
                writer.writerow([
                    device['id'], 'device', device['type'], device['os'], device['browser'],
                    device['fingerprint'], device['first_seen'], device['last_login'],
                    device['login_count'], device['fraud_flag']
                ])
        
        # Write ownership edges file
        owns_file = self.output_dir / "edges" / "ownership" / "owns.csv"
        with open(owns_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['~from', '~to', '~label', 'since:Date'])
            
            for edge in self.owns_edges:
                writer.writerow([edge['from'], edge['to'], 'OWNS', edge['since']])
        
        # Write usage edges file
        uses_file = self.output_dir / "edges" / "usage" / "uses.csv"
        with open(uses_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                '~from', '~to', '~label', 'first_used:Date', 
                'last_used:Date', 'usage_count:Int'
            ])
            
            for edge in self.uses_edges:
                writer.writerow([
                    edge['from'], edge['to'], 'USES', edge['first_used'],
                    edge['last_used'], edge['usage_count']
                ])
    
    def print_statistics(self):
        """Print generation statistics"""
        print(f"\n✅ Generated {self.region} banking data:")
        print(f"   👥 Users: {len(self.users)}")
        print(f"   🏦 Accounts: {len(self.accounts)}")
        print(f"   📱 Devices: {len(self.devices)}")
        print(f"   🔗 Ownership edges: {len(self.owns_edges)}")
        print(f"   🔗 Usage edges: {len(self.uses_edges)}")
        
        print(f"\n🕵️ Fraud patterns created:")
        for i, group in enumerate(self.shared_device_groups):
            device_ids = [d['id'] for d in group['devices']]
            print(f"   Group {i+1} ({group['type']}): {len(group['users'])} users sharing {device_ids}")
        
        # Device distribution
        device_counts = {}
        for edge in self.uses_edges:
            user = edge['from']
            device_counts[user] = device_counts.get(user, 0) + 1
        
        distribution = {}
        for count in device_counts.values():
            distribution[count] = distribution.get(count, 0) + 1
        
        print(f"\n📊 Device distribution:")
        for count, num_users in sorted(distribution.items()):
            print(f"   {num_users} users have {count} device(s)")
        
        # Check for users with 0 devices
        users_with_zero_devices = self.num_users - len(device_counts)
        if users_with_zero_devices > 0:
            print(f"   ❌ {users_with_zero_devices} users have 0 devices (device pool exhausted)")
        
        print(f"\n📱 Scalable device pool utilization:")
        print(f"   Total device capacity: {self.max_devices:,}")
        print(f"   Devices allocated: {len(self.allocated_devices):,}")
        print(f"   Devices in memory: {len(self.devices):,} (includes shared devices)")
        print(f"   Unused capacity: {self.max_devices - len(self.allocated_devices):,}")
        if self.max_devices > 0:
            utilization_rate = (len(self.allocated_devices) / self.max_devices * 100)
            print(f"   Utilization rate: {utilization_rate:.1f}%")
        
        # Memory efficiency stats
        memory_efficiency = (len(self.devices) / len(self.allocated_devices) * 100) if self.allocated_devices else 0
        print(f"   Memory efficiency: {memory_efficiency:.1f}% (lower is better for large datasets)")

def main():
    parser = argparse.ArgumentParser(description="Generate user data for fraud detection with Aerospike Graph CSV format")
    parser.add_argument("--users", type=int, default=100, help="Number of users to generate (default: 100)")
    parser.add_argument("--region", choices=['american', 'indian', 'en_GB', 'en_AU', 'zh_CN'], default='american',
                       help="Demographics region (default: american)")
    parser.add_argument("--output", default="./data/graph_csv", help="Output directory (default: ./data/graph_csv)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible data (default: 42)")
    
    args = parser.parse_args()
    
    # Set random seeds
    set_seeds(args.seed)
    
    # Performance tracking
    import time
    start_time = time.time()
    
    print(f"🚀 Starting scalable generation of {args.users:,} users...")
    
    # Generate data
    generator = UserDataGenerator(args.users, args.region, args.output)
    
    data_gen_start = time.time()
    generator.generate_all_data()
    data_gen_time = time.time() - data_gen_start
    
    csv_write_start = time.time()
    generator.write_csv_files()
    csv_write_time = time.time() - csv_write_start
    
    generator.print_statistics()
    
    total_time = time.time() - start_time
    
    print(f"\n⏱️ Performance Summary:")
    print(f"   Data generation: {data_gen_time:.2f}s ({args.users/data_gen_time:.0f} users/sec)")
    print(f"   CSV writing: {csv_write_time:.2f}s")
    print(f"   Total time: {total_time:.2f}s")
    
    print(f"\n📁 CSV files written to: {args.output}")
    print(f"📋 Ready for Aerospike Graph bulk loading!")

if __name__ == "__main__":
    main()
