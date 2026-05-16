import asyncio  
import redis.asyncio as aioredis  
async def main():  
    redis = aioredis.Redis(host='192.168.100.128', port=6379, db=2, decode_responses=True)  
    pubsub = redis.pubsub()  
    await pubsub.subscribe("werewolf:events:pubsub")  
    print("Subscribed to werewolf:events:pubsub")  
    async for message in pubsub.listen():  
        print(message)  
asyncio.run(main())  
