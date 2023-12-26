# Django view for handling image or text conversion with external services
import requests
import numpy as np
import csv
from glob import glob
from pathlib import Path
from statistics import mean
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from towhee import pipe, ops, DataCollection
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility
#from django.core.files.storage import FileSystemStorage

# Towhee parameters
MODEL = 'resnet50'
DEVICE = None # if None, use default device (cuda is enabled if available)

# Milvus parameters
HOST = '127.0.0.1'
PORT = '19530'
TOPK = 8 # number of results to return

INDEX_TYPE = 'IVF_FLAT'
METRIC_TYPE = 'L2'

# path to csv (column_1 indicates image path) OR a pattern of image paths
INSERT_SRC = 'reverse_image_search.csv'
QUERY_SRC = 'test/*/*.JPEG'

################################ Image-based search ##################################
def load_image(x):
    if x.endswith('csv'):
        with open(x) as f:
            reader = csv.reader(f)
            next(reader)
            for item in reader:
                yield item[1]
    else:
        for item in glob(x):
            yield item

p_embed = (
            pipe.input('src')
                .flat_map('src', 'img_path', load_image)
                .map('img_path', 'img', ops.image_decode())
                .map('img', 'vec', ops.image_embedding.timm(model_name=MODEL, device=DEVICE))
        )


p_insert = (
        p_embed.map(('img_path', 'vec'), 'mr', ops.ann_insert.milvus_client(
                    host=HOST,
                    port=PORT,
                    collection_name='image_based_search'
                    ))
          .output('mr')
    )

# Search pipeline
p_search_pre = (
        p_embed.map('vec', ('search_res'), ops.ann_search.milvus_client(
                    host=HOST, port=PORT, limit=TOPK,
                    collection_name='image_based_search'))
               .map('search_res', 'pred', lambda x: [str(Path(y[0]).resolve()) for y in x])
#                .output('img_path', 'pred')
)
p_search = p_search_pre.output('img_path', 'pred')
################################ Image-based search ##################################

################################ Text-based search ##################################
def read_csv(csv_path, encoding='utf-8-sig'):
    import csv
    with open(csv_path, 'r', encoding=encoding) as f:
        data = csv.DictReader(f)
        for line in data:
            yield int(line['id']), line['path']

multiModalInsertPipe = (
    pipe.input('csv_file')
    .flat_map('csv_file', ('id', 'path'), read_csv)
    .map('path', 'img', ops.image_decode.cv2('rgb'))
    .map('img', 'vec', ops.image_text_embedding.clip(model_name='clip_vit_base_patch16', modality='image', device=0))
    .map('vec', 'vec', lambda x: x / np.linalg.norm(x))
    .map(('path', 'vec'), (), ops.ann_insert.milvus_client(host='127.0.0.1', port='19530', collection_name='text_image_search'))
    .output()
)

multiModalSearchPipe = (
    pipe.input('text')
    .map('text', 'vec', ops.image_text_embedding.clip(model_name='clip_vit_base_patch16', modality='text'))
    .map('vec', 'vec', lambda x: x / np.linalg.norm(x))
    .map('vec', 'result', ops.ann_search.milvus_client(host='127.0.0.1', port='19530', collection_name='text_image_search', limit=5))
    .map('result', 'image_paths', lambda x: [item[0] for item in x])
    #.map('image_ids', 'images', read_image)
    .output('text', 'image_paths')
)

################################ Text-based search ##################################

def search_image_or_text(data, data_type, collection):
    if data_type == 'image':
        # Search for example query image(s)
        collection.load()
        dc = p_search(data)
        return dc
        
    elif data_type == 'text':  
        collection.load()
        dc = multiModalSearchPipe(data)
        return dc

# Create milvus collection (delete first if exists)
def create_milvus_collection(collection_name, dim):
    if utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
    
    fields = [
        FieldSchema(name='path', dtype=DataType.VARCHAR, description='path to image', max_length=500, 
                    is_primary=True, auto_id=False),
        FieldSchema(name='embedding', dtype=DataType.FLOAT_VECTOR, description='image embedding vectors', dim=dim)
    ]
    schema = CollectionSchema(fields=fields, description='image search')
    collection = Collection(name=collection_name, schema=schema)

    index_params = {
        'metric_type': METRIC_TYPE,
        'index_type': INDEX_TYPE,
        'params': {"nlist": dim}
    }
    collection.create_index(field_name='embedding', index_params=index_params)
    return collection

def initialize_milvus(collection_name, search_type):
    # Connect to Milvus service
    connections.connect(host=HOST, port=PORT)

    # Check if the collection already exists
    if utility.has_collection(collection_name):
        milvus_collection = Collection(name=collection_name)
        #print(f"Using existing collection: {COLLECTION_NAME}")
    elif search_type == 'image':
        # If not, create a new collection
        dim = 2048
        milvus_collection = create_milvus_collection(collection_name, dim)
        #print(f'A new collection created: {COLLECTION_NAME}')
        # Insert data
        p_insert(INSERT_SRC)
        #print('Number of data inserted:', milvus_collection.num_entities)

    elif search_type == 'text':
        # If not, create a new collection
        dim = 512
        milvus_collection = create_milvus_collection(collection_name, dim)
        print(f'A new collection created: {collection_name}')
        # Insert data
        multiModalInsertPipe(INSERT_SRC)
        #print('Number of data inserted:', milvus_collection.num_entities)
    return milvus_collection
    

@csrf_exempt
@require_http_methods(["POST"])
def image_based_search(request):
    # Connect to Milvus service
    collection_name = 'image_based_search'
    search_type = 'image'
    collection = initialize_milvus(collection_name, search_type)

    try:
        image = request.body.decode('utf-8')

        # Process image using ML model
        result_path_list = search_image_or_text(image, 'image', collection).to_list()
        
        # return result_path_list as response
        return JsonResponse({'message': 'Image processed successfully', 'stored_id': result_path_list})
    
    except Exception as e:
        # Handle any errors that occur during the process
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def text_based_search(request):
    # Placeholder
    #return JsonResponse({'message': 'Hit text_based_search'})
    
    collection_name = 'text_based_search'
    search_type = 'text'
    collection = initialize_milvus(collection_name, search_type)

    try:
        text = request.body.decode('utf-8')

        # Process image using ML model
        result_path_list = search_image_or_text(text, 'text', collection).to_list()
        
        # return result_path_list as response
        return JsonResponse({'message': 'Text processed successfully', 'stored_id': result_path_list})
    
    except Exception as e:
        # Handle any errors that occur during the process
        return JsonResponse({'error': str(e)}, status=500)
       

@csrf_exempt
@require_http_methods(["POST"])
def image_embedding_and_storage(request):

    collection = initialize_milvus()
    
    try:
        # Assuming the request body will contain the path to the image(s)
        image_paths = request.body.decode('utf-8').split('\n')  

        # Process each image and store the embeddings
        stored_ids = []
        for image_path in image_paths:
            
            # Use the Towhee pipeline to process and insert the image embedding into Milvus
            p_insert_result = p_insert(image_path)
            stored_ids.extend(p_insert_result)

        # Commit the changes to the Milvus database
        collection.load()

        # Return the list of Milvus primary keys as a response
        return JsonResponse({'message': 'Images processed and stored successfully', 'stored_ids': stored_ids})

    except Exception as e:
        # Handle any errors that occur during the process
        return JsonResponse({'error': str(e)}, status=500)
